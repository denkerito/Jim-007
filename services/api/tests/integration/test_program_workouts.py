from datetime import date

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.dependencies import get_uow_factory, get_workout_text_interpreter
from app.application.commands import (
    InterpretedExercise, InterpretationStatus, PerformedSetInput,
    PlannedExerciseInput, ProgramWorkoutInterpretation,
    WorkoutLogInterpretation, WorkoutStartInterpretation,
)
from app.infrastructure.database.models import Exercise, ProgramWorkout
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.main import app


class ProgramInterpreter:
    async def interpret_program(self, **kwargs):
        return ProgramWorkoutInterpretation(
            status=InterpretationStatus.READY,
            exercises=(
                PlannedExerciseInput(name="Panca piana", target_sets=3, target_repetitions=6, rest_seconds=180),
                PlannedExerciseInput(name="Spinte", target_sets=2, target_repetitions=8, rest_seconds=120),
            ),
        )

    async def interpret_start(self, *, programs, **kwargs):
        return WorkoutStartInterpretation(
            status=InterpretationStatus.READY, kind="program",
            program_workout_id=programs[0].id,
        )

    async def interpret_exercises(self, **kwargs):
        return WorkoutLogInterpretation(
            status=InterpretationStatus.READY,
            exercises=(InterpretedExercise(
                name="Panca piana",
                sets=tuple(
                    PerformedSetInput(repetitions=6, load_value="80", load_unit="kg")
                    for _ in range(3)
                ),
            ),),
        )


def _headers(key: str | None = None) -> dict[str, str]:
    result = {"Authorization": "Bearer integration-secret"}
    if key is not None:
        result["Idempotency-Key"] = key
    return result


@pytest.mark.asyncio
async def test_program_workout_lifecycle_and_last_exercise(session_factory, telegram_identity_factory) -> None:
    interpreter = ProgramInterpreter()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(session_factory)
    app.dependency_overrides[get_workout_text_interpreter] = lambda: interpreter
    transport = ASGITransport(app=app)
    base = {"provider": "telegram", "provider_subject": "99123"}
    await telegram_identity_factory(99123)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            created = await client.post(
                "/internal/program-events", headers=_headers("program:create:1"),
                json={**base, "action": "create", "day_number": 1, "alias": "push", "text": "panca 3x6 180s spinte 2x8 120s", "notes": "Forza"},
            )
            assert created.status_code == 200, created.text
            assert [item["target_sets"] for item in created.json()["program_workout"]["items"]] == [3, 2]
            async with session_factory() as session:
                assert await session.scalar(select(func.count()).select_from(Exercise)) == 0

            opened = await client.post(
                "/internal/workout-events", headers=_headers("workout:open:1"),
                json={**base, "action": "open", "text": "push"},
            )
            assert opened.status_code == 200, opened.text
            assert opened.json()["workout"]["program_workout"]["alias"] == "push"
            assert opened.json()["program_history"][0]["latest_performed_on"] is None

            status = await client.post("/internal/workout-status", headers=_headers(), json=base)
            assert status.json()["workout"]["program_workout"]["day_number"] == 1

            logged = await client.post(
                "/internal/workout-events", headers=_headers("workout:log:1"),
                json={**base, "action": "log", "text": "panca 80x6x3"},
            )
            assert logged.status_code == 200, logged.text
            completed = await client.post(
                "/internal/workout-events", headers=_headers("workout:end:1"),
                json={**base, "action": "complete"},
            )
            assert completed.status_code == 200, completed.text

            reopened = await client.post(
                "/internal/workout-events", headers=_headers("workout:open:2"),
                json={**base, "action": "open", "text": "1"},
            )
            history = reopened.json()["program_history"][0]
            assert history["latest_performed_on"] == date.today().isoformat()
            assert history["latest_occurrences"][0]["sets"][0]["load"]["value"] == "80.000"

            await client.post(
                "/internal/workout-events", headers=_headers("workout:cancel:2"),
                json={**base, "action": "cancel"},
            )
            edited = await client.post(
                "/internal/program-events", headers=_headers("program:edit:1"),
                json={**base, "action": "edit", "selector": "push", "text": "nuova scheda"},
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["kind"] == "edited"

            reset = await client.post(
                "/internal/program-events", headers=_headers("program:reset:1"),
                json={**base, "action": "new"},
            )
            assert reset.status_code == 200, reset.text
            async with session_factory() as session:
                active = await session.scalar(
                    select(func.count()).select_from(ProgramWorkout).where(ProgramWorkout.deactivated_at.is_(None))
                )
                assert active == 0

            reused = await client.post(
                "/internal/program-events", headers=_headers("program:create:2"),
                json={**base, "action": "create", "day_number": 1, "alias": "push", "text": "scheda nuova"},
            )
            assert reused.status_code == 200, reused.text
    finally:
        app.dependency_overrides.clear()
