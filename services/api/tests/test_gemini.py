import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.application.commands import (
    ExerciseCatalogItem,
    ExerciseQueryInterpretation,
    ExerciseResolutionStatus,
    FollowupInterpretationStatus,
    InterpretationStatus,
    InterpretedExercise,
    PerformedSetInput,
    ProgramExerciseResolution,
    ProgramExerciseResolutionInput,
    ProgramExerciseResolutionItem,
    PlannedExerciseInput,
    ProgramWorkoutInterpretation,
    WorkoutInterpretationContext,
    WorkoutLogInterpretation,
    WorkoutLogFollowupInterpretation,
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
    assert "una sola domanda" in kwargs["input"]
    assert adapter.workout_log_prompt_version == "workout-log-v2"


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


@pytest.mark.asyncio
async def test_gemini_resolves_history_query_only_against_catalog(monkeypatch) -> None:
    from uuid import uuid4

    exercise_id = uuid4()
    expected = ExerciseQueryInterpretation(
        status=ExerciseResolutionStatus.MATCHED,
        exercise_id=exercise_id,
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

    result = await adapter.resolve_exercise(
        text="panca",
        locale="it-IT",
        catalog=(ExerciseCatalogItem(id=exercise_id, name="Panca piana"),),
    )

    assert result == expected
    prompt = create.await_args.kwargs["input"]
    assert "panca" in prompt
    assert str(exercise_id) in prompt
    assert "Non inventare ID" in prompt


@pytest.mark.asyncio
async def test_gemini_accepts_program_resolution_without_status(monkeypatch) -> None:
    from uuid import uuid4

    item_id = uuid4()
    exercise_id = uuid4()
    expected = ProgramExerciseResolution(
        resolutions=(
            ProgramExerciseResolutionItem(
                item_id=item_id,
                exercise_id=exercise_id,
            ),
        )
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

    result = await adapter.resolve_program_exercises(
        items=(ProgramExerciseResolutionInput(item_id=item_id, name="Panca"),),
        locale="it-IT",
        catalog=(ExerciseCatalogItem(id=exercise_id, name="Panca piana"),),
    )

    assert result == expected
    assert str(item_id) in create.await_args.kwargs["input"]
    assert str(exercise_id) in create.await_args.kwargs["input"]


@pytest.mark.asyncio
async def test_gemini_program_clarification_ignores_partial_exercises(monkeypatch) -> None:
    expected = ProgramWorkoutInterpretation(
        status=InterpretationStatus.NEEDS_CLARIFICATION,
        exercises=(
            PlannedExerciseInput(
                name="Rematore", target_sets=2,
                target_repetitions=8, rest_seconds=120,
            ),
            PlannedExerciseInput(name="Lat machine", rest_seconds=120),
        ),
        clarification_message="Quante serie e ripetizioni per la lat machine?",
    )
    client, _ = _fake_client(expected.model_dump_json())
    monkeypatch.setattr("app.infrastructure.llm.gemini.genai.Client", lambda **_: client)
    adapter = GeminiWorkoutTextInterpreter(
        api_key="secret", model="gemini-3.5-flash-lite",
        timeout_seconds=8, max_output_tokens=4096, thinking_level="minimal",
    )

    result = await adapter.interpret_program(
        text="rematore 2x8 120s lat machine 120s",
        context=_context(), catalog=(),
    )

    assert result.status is InterpretationStatus.NEEDS_CLARIFICATION
    assert result.clarification_message == "Quante serie e ripetizioni per la lat machine?"


@pytest.mark.asyncio
async def test_gemini_followup_returns_terminal_full_payload(monkeypatch) -> None:
    expected = WorkoutLogFollowupInterpretation(
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
    client, create = _fake_client(expected.model_dump_json())
    monkeypatch.setattr("app.infrastructure.llm.gemini.genai.Client", lambda **_: client)
    adapter = GeminiWorkoutTextInterpreter(
        api_key="secret", model="gemini-3.5-flash-lite",
        timeout_seconds=8, max_output_tokens=4096, thinking_level="minimal",
    )

    result = await adapter.interpret_exercise_followup(
        original_text="panca piana",
        clarification_message="Quante serie e ripetizioni, e con quale carico?",
        answer_text="55x8 55x6 55x6",
        context=_context(),
        catalog=(),
    )

    assert result == expected
    prompt = create.await_args.kwargs["input"]
    assert "panca piana" in prompt
    assert "55x8 55x6 55x6" in prompt
    assert "Non chiedere un altro chiarimento" in prompt
    assert adapter.workout_log_followup_prompt_version == "workout-log-followup-v1"
