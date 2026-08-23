from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import get_uow_factory
from app.api.web_security import SESSION_COOKIE
from app.infrastructure.database.models import (
    AppUser,
    Exercise,
    PerformedSet,
    WebAccount,
    WebSession,
    Workout,
    WorkoutExercise,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.infrastructure.security import PasswordService, token_hash
from app.main import app


async def _web_session(session_factory, user_id) -> str:
    raw = "statistics-web-session"
    now = datetime.now(timezone.utc)
    async with session_factory() as session:
        session.add(WebAccount(
            user_id=user_id,
            email="statistics@example.com",
            normalized_email="statistics@example.com",
            password_hash=PasswordService().hash("a-secure-password"),
            email_verified_at=now,
        ))
        session.add(WebSession(
            user_id=user_id,
            token_hash=token_hash(raw),
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ))
        await session.commit()
    return raw


def _add_occurrence(session, *, user_id, workout_id, exercise_id, position, sets):
    occurrence_id = uuid4()
    session.add(WorkoutExercise(
        id=occurrence_id,
        user_id=user_id,
        workout_id=workout_id,
        exercise_id=exercise_id,
        log_batch_id=uuid4(),
        position=position,
    ))
    for number, (repetitions, load_kg) in enumerate(sets, start=1):
        session.add(PerformedSet(
            id=uuid4(),
            user_id=user_id,
            workout_exercise_id=occurrence_id,
            set_number=number,
            repetitions=repetitions,
            load_value=load_kg,
            load_unit="kg" if load_kg is not None else None,
            load_kg=load_kg,
        ))


@pytest.mark.asyncio
async def test_statistics_endpoints_aggregate_progress_and_exclude_drafts(
    session_factory, user_id
) -> None:
    today = datetime.now(ZoneInfo("Europe/Rome")).date()
    now = datetime.now(timezone.utc)
    bench_id, pullup_id, foreign_exercise_id = uuid4(), uuid4(), uuid4()
    foreign_user_id = uuid4()
    async with session_factory() as session:
        user = await session.get(AppUser, user_id)
        user.preferred_load_unit = "lb"
        session.add_all([
            Exercise(id=bench_id, user_id=user_id, name="Bench Press", normalized_name="bench press"),
            Exercise(id=pullup_id, user_id=user_id, name="Pull-up", normalized_name="pull-up"),
            AppUser(id=foreign_user_id),
            Exercise(id=foreign_exercise_id, user_id=foreign_user_id, name="Squat", normalized_name="squat"),
        ])
        workouts = [
            (today - timedelta(days=35), "completed"),
            (today - timedelta(days=20), "completed"),
            (today - timedelta(days=10), "completed"),
            (today, "completed"),
            (today, "completed"),
            (today, "draft"),
        ]
        workout_ids = []
        for index, (performed_on, status) in enumerate(workouts):
            workout_id = uuid4()
            workout_ids.append(workout_id)
            session.add(Workout(
                id=workout_id,
                user_id=user_id,
                performed_on=performed_on,
                status=status,
                created_at=now + timedelta(seconds=index),
                completed_at=now + timedelta(seconds=index) if status == "completed" else None,
            ))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[0], exercise_id=bench_id, position=1, sets=((8, Decimal("80")),))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[1], exercise_id=bench_id, position=1, sets=((8, Decimal("80")), (6, Decimal("0"))))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[1], exercise_id=bench_id, position=2, sets=((10, Decimal("70")),))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[2], exercise_id=bench_id, position=1, sets=((5, Decimal("90")),))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[3], exercise_id=bench_id, position=1, sets=((13, Decimal("85")),))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[3], exercise_id=pullup_id, position=2, sets=((15, None),))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[4], exercise_id=bench_id, position=1, sets=((1, Decimal("92")),))
        _add_occurrence(session, user_id=user_id, workout_id=workout_ids[5], exercise_id=bench_id, position=1, sets=((1, Decimal("200")),))
        await session.commit()

    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(session_factory)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/api/me/statistics/overview")).status_code == 401
            client.cookies.set(SESSION_COOKIE, await _web_session(session_factory, user_id))

            exercise = await client.get(
                f"/api/me/exercises/{bench_id}/statistics", params={"period": "4w"}
            )
            assert exercise.status_code == 200, exercise.text
            body = exercise.json()
            assert body["summary"]["session_count"] == 4
            assert body["summary"]["set_count"] == 6
            assert body["summary"]["repetition_count"] == 43
            assert body["summary"]["max_set_repetitions"] == 13
            assert body["summary"]["best_load"]["kilograms"] == "92.000"
            assert body["summary"]["best_load"]["unit"] == "lb"
            assert body["summary"]["best_load"]["value"] == "202.825"
            assert body["summary"]["best_estimated_one_rep_max"]["kilograms"] == "105.000"
            assert body["summary"]["best_estimated_one_rep_max"]["value"] == "231.485"
            assert body["summary"]["best_session_volume"]["kilogram_repetitions"] == "1340.000"
            assert body["summary"]["best_session_volume"]["value"] == "2954.194"
            assert len(body["series"]) == 4
            assert body["series"][0]["set_count"] == 3

            bodyweight = await client.get(
                f"/api/me/exercises/{pullup_id}/statistics", params={"period": "4w"}
            )
            assert bodyweight.status_code == 200
            assert bodyweight.json()["summary"]["best_load"] is None
            assert bodyweight.json()["summary"]["best_estimated_one_rep_max"] is None
            assert bodyweight.json()["summary"]["best_session_volume"] is None

            overview = await client.get("/api/me/statistics/overview", params={"period": "4w"})
            assert overview.status_code == 200, overview.text
            payload = overview.json()
            assert payload["current"]["workout_count"] == 4
            assert payload["current"]["active_day_count"] == 3
            assert payload["current"]["set_count"] == 7
            assert payload["current"]["repetition_count"] == 58
            assert payload["current"]["external_volume"]["kilogram_repetitions"] == "2987.000"
            assert payload["previous"]["workout_count"] == 1
            assert payload["bucket"] == "week"
            assert any(item["workout_count"] == 0 for item in payload["series"])
            assert payload["top_exercises"][0]["exercise_name"] == "Bench Press"
            assert payload["top_exercises"][0]["workout_count"] == 4
            assert len(payload["recent_records"]) == 1
            assert payload["recent_records"][0]["estimated_one_rep_max"]["kilograms"] == "105.000"

            all_time = await client.get("/api/me/statistics/overview", params={"period": "all"})
            assert all_time.status_code == 200
            assert all_time.json()["previous"] is None
            assert all_time.json()["bucket"] == "month"

            assert (await client.get(
                f"/api/me/exercises/{foreign_exercise_id}/statistics"
            )).status_code == 404
            assert (await client.get(
                "/api/me/statistics/overview", params={"period": "invalid"}
            )).status_code == 422
    finally:
        app.dependency_overrides.clear()
