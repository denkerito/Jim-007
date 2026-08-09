import asyncio
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
from app.infrastructure.database.models import (
    Exercise,
    PerformedSet,
    ProcessedCommand,
    Workout,
    WorkoutExercise,
)
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
async def test_telegram_workout_event_flow_and_replay(session_factory, telegram_identity_factory) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(12345)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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
    session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(98765)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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


@pytest.mark.asyncio
async def test_status_undo_batches_and_permanent_cancel(session_factory, telegram_identity_factory) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(22222)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": "22222"}

            no_status = await client.post(
                "/internal/workout-status",
                headers={"Authorization": "Bearer integration-secret"},
                json=base,
            )
            assert no_status.status_code == 200
            assert no_status.json() == {"kind": "none"}

            await client.post(
                "/internal/workout-events",
                headers=_headers("batch-open"),
                json={**base, "action": "open", "text": None},
            )
            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.READY,
                exercises=(
                    InterpretedExercise(
                        name="Squat",
                        sets=(PerformedSetInput(repetitions=5, load_value="100"),),
                    ),
                ),
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers("batch-one"),
                json={**base, "action": "log", "text": "squat 100x5"},
            )
            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.READY,
                exercises=(
                    InterpretedExercise(
                        name="Bench Press",
                        sets=(PerformedSetInput(repetitions=8, load_value="80"),),
                    ),
                    InterpretedExercise(
                        name="Lat Machine",
                        sets=(PerformedSetInput(repetitions=10, load_value="70"),),
                    ),
                ),
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers("batch-two"),
                json={**base, "action": "log", "text": "panca e lat machine"},
            )

            active_status = await client.post(
                "/internal/workout-status",
                headers={"Authorization": "Bearer integration-secret"},
                json=base,
            )
            assert active_status.status_code == 200
            assert active_status.json()["kind"] == "active"
            assert len(active_status.json()["workout"]["exercises"]) == 3

            undone = await client.post(
                "/internal/workout-events",
                headers=_headers("undo-two"),
                json={**base, "action": "undo", "text": None},
            )
            assert undone.status_code == 200, undone.text
            assert undone.json()["kind"] == "undone"
            assert [
                item["exercise"]["name"]
                for item in undone.json()["removed_exercises"]
            ] == ["Bench Press", "Lat Machine"]
            assert [
                item["exercise"]["name"]
                for item in undone.json()["workout"]["exercises"]
            ] == ["Squat"]

            undo_replay = await client.post(
                "/internal/workout-events",
                headers=_headers("undo-two"),
                json={**base, "action": "undo", "text": None},
            )
            assert undo_replay.status_code == 200
            assert undo_replay.json()["replayed"] is True
            assert undo_replay.json()["removed_exercises"] == []

            last_undo = await client.post(
                "/internal/workout-events",
                headers=_headers("undo-one"),
                json={**base, "action": "undo", "text": None},
            )
            assert last_undo.status_code == 200
            assert last_undo.json()["workout"]["exercises"] == []

            nothing = await client.post(
                "/internal/workout-events",
                headers=_headers("undo-empty"),
                json={**base, "action": "undo", "text": None},
            )
            assert nothing.status_code == 409
            assert nothing.json()["detail"]["code"] == "nothingtoundo"

            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.READY,
                exercises=(
                    InterpretedExercise(
                        name="Squat",
                        sets=(PerformedSetInput(repetitions=3, load_value="110"),),
                    ),
                ),
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers("log-before-cancel"),
                json={**base, "action": "log", "text": "squat 110x3"},
            )
            cancelled = await client.post(
                "/internal/workout-events",
                headers=_headers("cancel-draft"),
                json={**base, "action": "cancel", "text": None},
            )
            assert cancelled.status_code == 200
            assert cancelled.json()["kind"] == "cancelled"
            assert cancelled.json()["workout"] is None

            cancel_replay = await client.post(
                "/internal/workout-events",
                headers=_headers("cancel-draft"),
                json={**base, "action": "cancel", "text": None},
            )
            assert cancel_replay.status_code == 200
            assert cancel_replay.json()["replayed"] is True

            reopened = await client.post(
                "/internal/workout-events",
                headers=_headers("open-after-cancel"),
                json={**base, "action": "open", "text": None},
            )
            assert reopened.status_code == 200

        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Workout)) == 1
            assert (
                await session.scalar(select(func.count()).select_from(WorkoutExercise))
                == 0
            )
            assert (
                await session.scalar(select(func.count()).select_from(PerformedSet)) == 0
            )
            assert await session.scalar(select(func.count()).select_from(Exercise)) == 3
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_cancel_is_serialized_and_leaves_one_claim(
    session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(33333)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": "33333"}
            await client.post(
                "/internal/workout-events",
                headers=_headers("concurrent-open"),
                json={**base, "action": "open", "text": None},
            )

            first, second = await asyncio.gather(
                client.post(
                    "/internal/workout-events",
                    headers=_headers("concurrent-cancel-one"),
                    json={**base, "action": "cancel", "text": None},
                ),
                client.post(
                    "/internal/workout-events",
                    headers=_headers("concurrent-cancel-two"),
                    json={**base, "action": "cancel", "text": None},
                ),
            )
            assert sorted((first.status_code, second.status_code)) == [200, 409]
            failed = first if first.status_code == 409 else second
            assert failed.json()["detail"]["code"] == "noactiveworkout"

        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(Workout)) == 0
            cancel_claims = await session.scalar(
                select(func.count())
                .select_from(ProcessedCommand)
                .where(ProcessedCommand.operation == "cancel_workout")
            )
            assert cancel_claims == 1
    finally:
        app.dependency_overrides.clear()
