import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.api.dependencies import get_uow_factory
from app.api.web_security import SESSION_COOKIE
from app.application.exercises import CreateExercise, RenameExercise
from app.domain.exceptions import ExerciseNameConflictError
from app.infrastructure.database.models import (
    AppUser,
    Exercise as ExerciseRecord,
    ProgramWorkout,
    ProgramWorkoutItem,
    WebAccount,
    WebSession,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.infrastructure.security import PasswordService, token_hash
from app.main import app


async def _web_session(session_factory, user_id) -> str:
    raw = "exercise-creation-session"
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(
            WebAccount(
                user_id=user_id,
                email="exercise-creation@example.com",
                normalized_email="exercise-creation@example.com",
                password_hash=PasswordService().hash("a-secure-password"),
                email_verified_at=now,
            )
        )
        session.add(
            WebSession(
                user_id=user_id,
                token_hash=token_hash(raw),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()
    return raw


@pytest.mark.asyncio
async def test_web_can_create_or_resolve_a_personal_exercise(
    session_factory, user_id
) -> None:
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    transport = ASGITransport(app=app)
    origin = "http://localhost:3000"
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unauthorized = await client.post(
                "/api/me/exercises",
                headers={"Origin": origin},
                json={"name": "Squat"},
            )
            assert unauthorized.status_code == 401

            raw_session = await _web_session(session_factory, user_id)
            client.cookies.set(SESSION_COOKIE, raw_session)
            session = await client.get("/api/auth/session")
            csrf = session.json()["csrf_token"]
            headers = {"Origin": origin, "X-CSRF-Token": csrf}

            missing_origin = await client.post(
                "/api/me/exercises",
                headers={"X-CSRF-Token": csrf},
                json={"name": "Squat"},
            )
            assert missing_origin.status_code == 403
            assert missing_origin.json()["detail"]["code"] == "invalid_origin"

            missing_csrf = await client.post(
                "/api/me/exercises",
                headers={"Origin": origin},
                json={"name": "Squat"},
            )
            assert missing_csrf.status_code == 403
            assert missing_csrf.json()["detail"]["code"] == "invalid_csrf_token"

            created = await client.post(
                "/api/me/exercises",
                headers=headers,
                json={"name": "  Bench\t Press  "},
            )
            assert created.status_code == 201, created.text
            assert created.json()["created"] is True
            assert created.json()["exercise"]["name"] == "Bench Press"

            duplicate = await client.post(
                "/api/me/exercises",
                headers=headers,
                json={"name": "bench press"},
            )
            assert duplicate.status_code == 200, duplicate.text
            assert duplicate.json()["created"] is False
            assert duplicate.json()["exercise"] == created.json()["exercise"]

            invalid = await client.post(
                "/api/me/exercises",
                headers=headers,
                json={"name": "x" * 256},
            )
            assert invalid.status_code == 422

            catalog = await client.get("/api/me/exercises")
            assert catalog.json()["items"] == [created.json()["exercise"]]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_creation_returns_one_personal_exercise(
    session_factory, user_id
) -> None:
    factory = lambda: SqlAlchemyUnitOfWork(session_factory)

    first, second = await asyncio.gather(
        CreateExercise(factory).execute(user_id=user_id, name="Lat Pulldown"),
        CreateExercise(factory).execute(user_id=user_id, name="  lat pulldown "),
    )

    assert first.exercise.id == second.exercise.id
    assert {first.created, second.created} == {True, False}


@pytest.mark.asyncio
async def test_same_normalized_name_is_isolated_by_user(
    session_factory, user_id
) -> None:
    other_user = uuid4()
    async with session_factory() as session:
        from app.infrastructure.database.models import AppUser

        session.add(AppUser(id=other_user))
        await session.commit()
    factory = lambda: SqlAlchemyUnitOfWork(session_factory)

    first = await CreateExercise(factory).execute(user_id=user_id, name="Deadlift")
    second = await CreateExercise(factory).execute(user_id=other_user, name="deadlift")

    assert first.created is True
    assert second.created is True
    assert first.exercise.id != second.exercise.id


@pytest.mark.asyncio
async def test_web_can_rename_an_owned_exercise_without_rewriting_programs(
    session_factory, user_id
) -> None:
    factory = lambda: SqlAlchemyUnitOfWork(session_factory)
    bench = await CreateExercise(factory).execute(user_id=user_id, name="Bench Press")
    await CreateExercise(factory).execute(user_id=user_id, name="Squat")
    other_user_id = uuid4()
    async with session_factory() as session:
        session.add(AppUser(id=other_user_id))
        await session.commit()
    foreign = await CreateExercise(factory).execute(
        user_id=other_user_id, name="Deadlift"
    )
    program_id = uuid4()
    async with session_factory() as session:
        session.add(
            ProgramWorkout(
                id=program_id,
                user_id=user_id,
                day_number=1,
                alias="Push",
                normalized_alias="push",
                items=[
                    ProgramWorkoutItem(
                        user_id=user_id,
                        position=1,
                        exercise_name="Bench Press",
                        normalized_exercise_name="bench press",
                        exercise_id=bench.exercise.id,
                        target_sets=3,
                        target_repetitions=8,
                    )
                ],
            )
        )
        await session.commit()

    app.dependency_overrides[get_uow_factory] = lambda: factory
    transport = ASGITransport(app=app)
    origin = "http://localhost:3000"
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unauthorized = await client.patch(
                f"/api/me/exercises/{bench.exercise.id}",
                headers={"Origin": origin},
                json={"name": "BENCH PRESS"},
            )
            assert unauthorized.status_code == 401

            raw_session = await _web_session(session_factory, user_id)
            client.cookies.set(SESSION_COOKIE, raw_session)
            auth = await client.get("/api/auth/session")
            csrf = auth.json()["csrf_token"]
            headers = {"Origin": origin, "X-CSRF-Token": csrf}

            missing_origin = await client.patch(
                f"/api/me/exercises/{bench.exercise.id}",
                headers={"X-CSRF-Token": csrf},
                json={"name": "BENCH PRESS"},
            )
            assert missing_origin.status_code == 403

            missing_csrf = await client.patch(
                f"/api/me/exercises/{bench.exercise.id}",
                headers={"Origin": origin},
                json={"name": "BENCH PRESS"},
            )
            assert missing_csrf.status_code == 403

            renamed = await client.patch(
                f"/api/me/exercises/{bench.exercise.id}",
                headers=headers,
                json={"name": "  BENCH\t PRESS  "},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json() == {
                "id": str(bench.exercise.id),
                "name": "BENCH PRESS",
                "normalized_name": "bench press",
            }

            catalog = await client.get("/api/me/exercises")
            catalog_item = next(
                item
                for item in catalog.json()["items"]
                if item["id"] == str(bench.exercise.id)
            )
            assert catalog_item["name"] == "BENCH PRESS"
            history = await client.get(
                f"/api/me/exercises/{bench.exercise.id}/history"
            )
            assert history.json()["exercise"]["name"] == "BENCH PRESS"
            statistics = await client.get(
                f"/api/me/exercises/{bench.exercise.id}/statistics"
            )
            assert statistics.json()["exercise"]["name"] == "BENCH PRESS"

            duplicate = await client.patch(
                f"/api/me/exercises/{bench.exercise.id}",
                headers=headers,
                json={"name": " squat "},
            )
            assert duplicate.status_code == 409
            assert duplicate.json()["detail"]["code"] == "exercise_name_conflict"

            invalid = await client.patch(
                f"/api/me/exercises/{bench.exercise.id}",
                headers=headers,
                json={"name": "x" * 256},
            )
            assert invalid.status_code == 422

            not_owned = await client.patch(
                f"/api/me/exercises/{foreign.exercise.id}",
                headers=headers,
                json={"name": "Romanian Deadlift"},
            )
            assert not_owned.status_code == 404

        async with session_factory() as session:
            programmed_name = await session.scalar(
                select(ProgramWorkoutItem.exercise_name).where(
                    ProgramWorkoutItem.program_workout_id == program_id
                )
            )
            assert programmed_name == "Bench Press"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_renames_preserve_unique_personal_names(
    session_factory, user_id
) -> None:
    factory = lambda: SqlAlchemyUnitOfWork(session_factory)
    first = await CreateExercise(factory).execute(user_id=user_id, name="Chest Press")
    second = await CreateExercise(factory).execute(user_id=user_id, name="Pectoral Press")

    results = await asyncio.gather(
        RenameExercise(factory).execute(
            user_id=user_id,
            exercise_id=first.exercise.id,
            name="Machine Press",
        ),
        RenameExercise(factory).execute(
            user_id=user_id,
            exercise_id=second.exercise.id,
            name=" machine press ",
        ),
        return_exceptions=True,
    )

    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert sum(isinstance(result, ExerciseNameConflictError) for result in results) == 1
    async with session_factory() as session:
        matching_ids = (
            await session.scalars(
                select(ExerciseRecord.id).where(
                    ExerciseRecord.user_id == user_id,
                    ExerciseRecord.normalized_name == "machine press",
                )
            )
        ).all()
        assert len(matching_ids) == 1
