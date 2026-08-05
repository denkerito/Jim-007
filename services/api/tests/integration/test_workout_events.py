from datetime import date
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.dependencies import get_uow_factory, get_workout_text_interpreter
from app.application.commands import (
    InterpretedExercise,
    InterpretationStatus,
    PerformedSetInput,
    WorkoutDateInterpretation,
    WorkoutLogInterpretation,
)
from app.infrastructure.database.models import Exercise, PerformedSet, WorkoutExercise
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.main import app


class FakeInterpreter:
    def __init__(self) -> None:
        self.date_result = WorkoutDateInterpretation(
            status=InterpretationStatus.READY,
            performed_on=date(2026, 8, 4),
        )
        self.log_result = WorkoutLogInterpretation(
            status=InterpretationStatus.READY,
            exercises=(
                InterpretedExercise(
                    name="Bench Press",
                    sets=(
                        PerformedSetInput(repetitions=8, load_value="80"),
                        PerformedSetInput(repetitions=7, load_value="80"),
                    ),
                ),
                InterpretedExercise(
                    name="Lat Machine",
                    sets=tuple(
                        PerformedSetInput(repetitions=10, load_value="70")
                        for _ in range(3)
                    ),
                ),
            ),
        )
        self.date_calls = 0
        self.log_calls = 0

    async def interpret_date(self, **kwargs):
        self.date_calls += 1
        return self.date_result

    async def interpret_exercises(self, **kwargs):
        self.log_calls += 1
        return self.log_result


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": "Bearer integration-secret",
        "Idempotency-Key": key,
    }


@pytest.mark.asyncio
async def test_telegram_workout_event_flow_and_replay(session_factory) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            identity = {
                "telegram_user_id": 12345,
                "username": "gym_user",
                "display_name": "Gym User",
            }
            registered = await client.post(
                "/internal/identities/telegram",
                headers={"Authorization": "Bearer integration-secret"},
                json=identity,
            )
            assert registered.status_code == 201

            base = {"provider": "telegram", "provider_subject": "12345"}
            opened = await client.post(
                "/internal/workout-events",
                headers=_headers("telegram:update:1"),
                json={**base, "action": "open", "text": "ieri"},
            )
            assert opened.status_code == 200, opened.text
            assert opened.json()["kind"] == "opened"
            assert opened.json()["workout"]["performed_on"] == "2026-08-04"

            logged = await client.post(
                "/internal/workout-events",
                headers=_headers("telegram:update:2"),
                json={**base, "action": "log", "text": "panca e lat machine"},
            )
            assert logged.status_code == 200, logged.text
            assert len(logged.json()["added_exercises"]) == 2
            assert [len(item["sets"]) for item in logged.json()["added_exercises"]] == [2, 3]

            replay = await client.post(
                "/internal/workout-events",
                headers=_headers("telegram:update:2"),
                json={**base, "action": "log", "text": "panca e lat machine"},
            )
            assert replay.status_code == 200
            assert replay.json()["replayed"] is True
            assert replay.json()["added_exercises"] == []
            assert interpreter.log_calls == 1

            completed = await client.post(
                "/internal/workout-events",
                headers=_headers("telegram:update:3"),
                json={**base, "action": "complete", "text": None},
            )
            assert completed.status_code == 200, completed.text
            assert completed.json()["workout"]["status"] == "completed"
            assert len(completed.json()["workout"]["exercises"]) == 2
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_clarification_and_atomic_invalid_catalog_id_write_nothing(
    session_factory,
) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/internal/identities/telegram",
                headers={"Authorization": "Bearer integration-secret"},
                json={"telegram_user_id": 98765},
            )
            base = {"provider": "telegram", "provider_subject": "98765"}
            await client.post(
                "/internal/workout-events",
                headers=_headers("open"),
                json={**base, "action": "open", "text": None},
            )

            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.NEEDS_CLARIFICATION,
                clarification_message="Quante ripetizioni hai eseguito?",
            )
            clarification = await client.post(
                "/internal/workout-events",
                headers=_headers("clarify"),
                json={**base, "action": "log", "text": "panca pesante"},
            )
            assert clarification.status_code == 200
            assert clarification.json()["kind"] == "needs_clarification"

            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.READY,
                exercises=(
                    InterpretedExercise(
                        name="Squat",
                        sets=(PerformedSetInput(repetitions=5, load_value="100"),),
                    ),
                    InterpretedExercise(
                        catalog_exercise_id=uuid4(),
                        name="Unknown",
                        sets=(PerformedSetInput(repetitions=8),),
                    ),
                ),
            )
            invalid = await client.post(
                "/internal/workout-events",
                headers=_headers("invalid-catalog"),
                json={**base, "action": "log", "text": "squat e unknown"},
            )
            assert invalid.status_code == 404

        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Exercise)) == 0
            assert await session.scalar(select(func.count()).select_from(WorkoutExercise)) == 0
            assert await session.scalar(select(func.count()).select_from(PerformedSet)) == 0
    finally:
        app.dependency_overrides.clear()
