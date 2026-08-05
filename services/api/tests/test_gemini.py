import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.commands import (
    InterpretedExercise,
    InterpretationStatus,
    PerformedSetInput,
    WorkoutInterpretationContext,
    WorkoutLogInterpretation,
)
from app.domain.exceptions import LlmInvalidResponseError, LlmTimeoutError
from app.domain.models import LoadUnit
from app.infrastructure.llm.gemini import GeminiWorkoutTextInterpreter


def _context():
    from datetime import date

    return WorkoutInterpretationContext(
        locale="it-IT",
        timezone="Europe/Rome",
        current_date=date(2026, 8, 5),
        preferred_load_unit=LoadUnit.KG,
    )


def _fake_client(output_text: str):
    interaction = SimpleNamespace(output_text=output_text, usage=None)
    create = AsyncMock(return_value=interaction)
    aio = SimpleNamespace(
        interactions=SimpleNamespace(create=create),
        aclose=AsyncMock(),
    )
    return SimpleNamespace(aio=aio), create


@pytest.mark.asyncio
async def test_gemini_uses_interactions_structured_output_without_storage(monkeypatch) -> None:
    expected = WorkoutLogInterpretation(
        status=InterpretationStatus.READY,
        exercises=(
            InterpretedExercise(
                name="Bench Press",
                sets=(PerformedSetInput(repetitions=8, load_value="80"),),
            ),
        ),
    )
    client, create = _fake_client(expected.model_dump_json())
    monkeypatch.setattr("app.infrastructure.llm.gemini.genai.Client", lambda **_: client)
    adapter = GeminiWorkoutTextInterpreter(
        api_key="secret",
        model="gemini-3.5-flash-lite",
        timeout_seconds=8,
        max_output_tokens=4096,
        thinking_level="minimal",
    )

    result = await adapter.interpret_exercises(
        text="panca 80x8",
        context=_context(),
        catalog=(),
    )

    assert result == expected
    kwargs = create.await_args.kwargs
    assert kwargs["model"] == "gemini-3.5-flash-lite"
    assert kwargs["store"] is False
    assert kwargs["generation_config"] == {
        "thinking_level": "minimal",
        "max_output_tokens": 4096,
    }
    assert kwargs["response_format"]["mime_type"] == "application/json"
    assert "panca 80x8" in kwargs["input"]


@pytest.mark.asyncio
async def test_gemini_maps_empty_and_timed_out_responses(monkeypatch) -> None:
    client, create = _fake_client("")
    monkeypatch.setattr("app.infrastructure.llm.gemini.genai.Client", lambda **_: client)
    adapter = GeminiWorkoutTextInterpreter(
        api_key="secret",
        model="gemini-3.5-flash-lite",
        timeout_seconds=8,
        max_output_tokens=4096,
        thinking_level="minimal",
    )
    with pytest.raises(LlmInvalidResponseError):
        await adapter.interpret_exercises(text="panca", context=_context(), catalog=())

    async def slow_create(**kwargs):
        await asyncio.sleep(0.02)

    create.side_effect = slow_create
    adapter._timeout_seconds = 0.001
    with pytest.raises(LlmTimeoutError):
        await adapter.interpret_exercises(text="panca", context=_context(), catalog=())
