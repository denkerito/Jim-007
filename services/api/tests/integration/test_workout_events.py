import asyncio
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update

from app.api.dependencies import get_uow_factory, get_workout_text_interpreter
from app.application.commands import (
    InterpretedExercise,
    FollowupInterpretationStatus,
    InterpretationStatus,
    PerformedSetInput,
    WorkoutDateInterpretation,
    WorkoutLogInterpretation,
    WorkoutLogFollowupInterpretation,
)
from app.infrastructure.database.models import (
    Exercise,
    PerformedSet,
    ProcessedCommand,
    Workout,
    WorkoutExercise,
    WorkoutLogClarification,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.main import app


class FakeInterpreter:
    model_name = "fake-model"
    workout_log_prompt_version = "workout-log-v2"
    workout_log_followup_prompt_version = "workout-log-followup-v1"

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
        self.followup_result = WorkoutLogFollowupInterpretation(
            status=FollowupInterpretationStatus.READY,
            exercises=self.log_result.exercises,
        )
        self.followup_calls = 0
        self.followup_arguments = []

    async def interpret_date(self, **kwargs):
        self.date_calls += 1
        return self.date_result

    async def interpret_exercises(self, **kwargs):
        self.log_calls += 1
        return self.log_result

    async def interpret_exercise_followup(self, **kwargs):
        self.followup_calls += 1
        self.followup_arguments.append(kwargs)
        return self.followup_result


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

            interpreter.followup_result = WorkoutLogFollowupInterpretation(
                status=FollowupInterpretationStatus.READY,
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
async def test_single_followup_resolves_clarification_and_replays_without_new_llm_calls(
    session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    interpreter.log_result = WorkoutLogInterpretation(
        status=InterpretationStatus.NEEDS_CLARIFICATION,
        clarification_message="Quante serie e ripetizioni, e con quale carico?",
    )
    interpreter.followup_result = WorkoutLogFollowupInterpretation(
        status=FollowupInterpretationStatus.READY,
        exercises=(
            InterpretedExercise(
                name="Panca piana",
                sets=(
                    PerformedSetInput(repetitions=8, load_value="55"),
                    PerformedSetInput(repetitions=6, load_value="55"),
                    PerformedSetInput(repetitions=6, load_value="55"),
                ),
            ),
        ),
    )
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(44444)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": "44444"}
            await client.post(
                "/internal/workout-events",
                headers=_headers("clarification-open"),
                json={**base, "action": "open", "text": None},
            )
            first = await client.post(
                "/internal/workout-events",
                headers=_headers("clarification-first"),
                json={**base, "action": "log", "text": "panca piana"},
            )
            assert first.status_code == 200, first.text
            assert first.json()["kind"] == "needs_clarification"

            first_replay = await client.post(
                "/internal/workout-events",
                headers=_headers("clarification-first"),
                json={**base, "action": "log", "text": "panca piana"},
            )
            assert first_replay.status_code == 200
            assert first_replay.json()["kind"] == "needs_clarification"
            assert first_replay.json()["replayed"] is True
            assert interpreter.log_calls == 1

            resolved = await client.post(
                "/internal/workout-events",
                headers=_headers("clarification-answer"),
                json={**base, "action": "log", "text": "55x8 55x6 55x6"},
            )
            assert resolved.status_code == 200, resolved.text
            assert resolved.json()["kind"] == "logged"
            assert len(resolved.json()["added_exercises"][0]["sets"]) == 3
            assert interpreter.followup_calls == 1
            assert interpreter.followup_arguments[0]["original_text"] == "panca piana"

            resolved_replay = await client.post(
                "/internal/workout-events",
                headers=_headers("clarification-answer"),
                json={**base, "action": "log", "text": "55x8 55x6 55x6"},
            )
            assert resolved_replay.status_code == 200
            assert resolved_replay.json()["replayed"] is True
            assert interpreter.followup_calls == 1

            initial_after_resolution = await client.post(
                "/internal/workout-events",
                headers=_headers("clarification-first"),
                json={**base, "action": "log", "text": "panca piana"},
            )
            assert initial_after_resolution.json()["kind"] == "logged"
            assert initial_after_resolution.json()["replayed"] is True

        async with session_factory() as session:
            stored = await session.scalar(select(WorkoutLogClarification))
            assert stored is not None
            assert stored.status == "resolved"
            assert stored.original_text is None
            assert stored.clarification_message is None
            assert await session.scalar(select(func.count()).select_from(PerformedSet)) == 3
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unclear_followup_requires_full_rewrite_and_next_message_starts_fresh(
    session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    interpreter.log_result = WorkoutLogInterpretation(
        status=InterpretationStatus.NEEDS_CLARIFICATION,
        clarification_message="Quante serie e ripetizioni?",
    )
    interpreter.followup_result = WorkoutLogFollowupInterpretation(
        status=FollowupInterpretationStatus.REWRITE_REQUIRED
    )
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(55555)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": "55555"}
            await client.post(
                "/internal/workout-events",
                headers=_headers("rewrite-open"),
                json={**base, "action": "open", "text": None},
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers("rewrite-first"),
                json={**base, "action": "log", "text": "panca"},
            )
            unclear = await client.post(
                "/internal/workout-events",
                headers=_headers("rewrite-answer"),
                json={**base, "action": "log", "text": "pesante"},
            )
            assert unclear.status_code == 200, unclear.text
            assert unclear.json() == {
                "kind": "rewrite_required",
                "replayed": False,
                "workout": None,
                "added_exercises": [],
                "removed_exercises": [],
                "clarification_message": (
                    "Non riesco ancora a interpretarlo. Riscrivi l'intero esercizio."
                ),
                "program_history": [],
            }

            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.READY,
                exercises=(
                    InterpretedExercise(
                        name="Panca piana",
                        sets=(PerformedSetInput(repetitions=8, load_value="55"),),
                    ),
                ),
            )
            fresh = await client.post(
                "/internal/workout-events",
                headers=_headers("rewrite-fresh"),
                json={**base, "action": "log", "text": "panca piana 55x8"},
            )
            assert fresh.status_code == 200, fresh.text
            assert fresh.json()["kind"] == "logged"
            assert interpreter.log_calls == 2
            assert interpreter.followup_calls == 1

        async with session_factory() as session:
            stored = await session.scalar(select(WorkoutLogClarification))
            assert stored is not None
            assert stored.status == "rewrite_required"
            assert stored.original_text is None
            assert stored.clarification_message is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_expired_clarification_is_scrubbed_and_new_text_is_a_fresh_log(
    session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    interpreter.log_result = WorkoutLogInterpretation(
        status=InterpretationStatus.NEEDS_CLARIFICATION,
        clarification_message="Quale esercizio?",
    )
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(66666)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": "66666"}
            await client.post(
                "/internal/workout-events",
                headers=_headers("expired-open"),
                json={**base, "action": "open", "text": None},
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers("expired-first"),
                json={**base, "action": "log", "text": "55x8"},
            )
            past = datetime.now(timezone.utc) - timedelta(hours=1)
            async with session_factory() as session:
                await session.execute(
                    update(WorkoutLogClarification).values(
                        created_at=past - timedelta(minutes=15),
                        expires_at=past,
                    )
                )
                await session.commit()

            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.READY,
                exercises=(
                    InterpretedExercise(
                        name="Panca piana",
                        sets=(PerformedSetInput(repetitions=8, load_value="55"),),
                    ),
                ),
            )
            fresh = await client.post(
                "/internal/workout-events",
                headers=_headers("expired-fresh"),
                json={**base, "action": "log", "text": "panca piana 55x8"},
            )
            assert fresh.status_code == 200, fresh.text
            assert fresh.json()["kind"] == "logged"
            assert interpreter.followup_calls == 0
            assert interpreter.log_calls == 2

        async with session_factory() as session:
            stored = await session.scalar(select(WorkoutLogClarification))
            assert stored is not None
            assert stored.status == "expired"
            assert stored.original_text is None
            assert stored.clarification_message is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["undo", "complete"])
async def test_successful_workout_mutation_cancels_pending_clarification(
    action, session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(77770 if action == "undo" else 77771)
    try:
        subject = "77770" if action == "undo" else "77771"
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": subject}
            await client.post(
                "/internal/workout-events",
                headers=_headers(f"{action}-open"),
                json={**base, "action": "open", "text": None},
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers(f"{action}-logged"),
                json={**base, "action": "log", "text": "panca 80x8"},
            )
            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.NEEDS_CLARIFICATION,
                clarification_message="Quante ripetizioni?",
            )
            pending = await client.post(
                "/internal/workout-events",
                headers=_headers(f"{action}-pending"),
                json={**base, "action": "log", "text": "squat"},
            )
            assert pending.json()["kind"] == "needs_clarification"

            mutated = await client.post(
                "/internal/workout-events",
                headers=_headers(f"{action}-mutation"),
                json={**base, "action": action, "text": None},
            )
            assert mutated.status_code == 200, mutated.text

        async with session_factory() as session:
            stored = await session.scalar(select(WorkoutLogClarification))
            assert stored is not None
            assert stored.status == "cancelled"
            assert stored.original_text is None
            assert stored.clarification_message is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_direct_exercise_add_cancels_pending_clarification(
    session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    user_id = await telegram_identity_factory(77772)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": "77772"}
            opened = await client.post(
                "/internal/workout-events",
                headers=_headers("direct-add-open"),
                json={**base, "action": "open", "text": None},
            )
            workout_id = opened.json()["workout"]["id"]
            interpreter.log_result = WorkoutLogInterpretation(
                status=InterpretationStatus.NEEDS_CLARIFICATION,
                clarification_message="Quante ripetizioni?",
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers("direct-add-pending"),
                json={**base, "action": "log", "text": "squat"},
            )

            added = await client.post(
                f"/users/{user_id}/workouts/{workout_id}/exercises",
                headers=_headers("direct-add-exercise"),
                json={
                    "exercise": {"kind": "new", "name": "Squat"},
                    "sets": [{"repetitions": 5, "load_value": "100"}],
                },
            )
            assert added.status_code == 201, added.text

        async with session_factory() as session:
            stored = await session.scalar(select(WorkoutLogClarification))
            assert stored is not None and stored.status == "cancelled"
            assert stored.original_text is None
            assert stored.clarification_message is None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_followups_persist_only_once(
    session_factory, telegram_identity_factory,
) -> None:
    interpreter = FakeInterpreter()
    interpreter.log_result = WorkoutLogInterpretation(
        status=InterpretationStatus.NEEDS_CLARIFICATION,
        clarification_message="Quante ripetizioni e con quale carico?",
    )
    interpreter.followup_result = WorkoutLogFollowupInterpretation(
        status=FollowupInterpretationStatus.READY,
        exercises=(
            InterpretedExercise(
                name="Panca piana",
                sets=(PerformedSetInput(repetitions=8, load_value="55"),),
            ),
        ),
    )
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    await telegram_identity_factory(88888)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            base = {"provider": "telegram", "provider_subject": "88888"}
            await client.post(
                "/internal/workout-events",
                headers=_headers("concurrent-followup-open"),
                json={**base, "action": "open", "text": None},
            )
            await client.post(
                "/internal/workout-events",
                headers=_headers("concurrent-followup-first"),
                json={**base, "action": "log", "text": "panca piana"},
            )
            first, second = await asyncio.gather(
                client.post(
                    "/internal/workout-events",
                    headers=_headers("concurrent-followup-a"),
                    json={**base, "action": "log", "text": "55x8"},
                ),
                client.post(
                    "/internal/workout-events",
                    headers=_headers("concurrent-followup-b"),
                    json={**base, "action": "log", "text": "55x8"},
                ),
            )
            assert sorted((first.status_code, second.status_code)) == [200, 409]

        async with session_factory() as session:
            assert await session.scalar(select(func.count()).select_from(PerformedSet)) == 1
            stored = await session.scalar(select(WorkoutLogClarification))
            assert stored is not None and stored.status == "resolved"
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
