"""Opt-in live smoke test; excluded unless explicitly enabled."""

import os
from datetime import date

import pytest

from app.application.commands import InterpretationStatus, WorkoutInterpretationContext
from app.domain.models import LoadUnit
from app.infrastructure.llm.gemini import GeminiWorkoutTextInterpreter


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_GEMINI_SMOKE") != "1",
    reason="set RUN_GEMINI_SMOKE=1 to call the real Gemini API",
)


@pytest.mark.asyncio
async def test_live_gemini_structured_workout_interpretation() -> None:
    api_key = os.environ["GEMINI_API_KEY"]
    adapter = GeminiWorkoutTextInterpreter(
        api_key=api_key,
        model=os.getenv("LLM_MODEL", "gemini-3.5-flash-lite"),
        timeout_seconds=8,
        max_output_tokens=4096,
        thinking_level="minimal",
    )
    try:
        result = await adapter.interpret_exercises(
            text="panca 80x8 80x8 80x7 e lat machine 70x10x3",
            context=WorkoutInterpretationContext(
                locale="it-IT",
                timezone="Europe/Rome",
                current_date=date.today(),
                preferred_load_unit=LoadUnit.KG,
            ),
            catalog=(),
        )
    finally:
        await adapter.close()

    assert result.status is InterpretationStatus.READY
    assert len(result.exercises) == 2
    assert [len(item.sets) for item in result.exercises] == [3, 3]
