import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_uow_factory
from app.api.web_security import SESSION_COOKIE
from app.application.exercises import CreateExercise
from app.infrastructure.database.models import WebAccount, WebSession
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
