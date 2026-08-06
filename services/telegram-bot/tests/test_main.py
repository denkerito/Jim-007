from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from app.backend import (
    BackendError,
    ExerciseSummary,
    HistoryQueryResult,
    RegistrationResult,
    SetSummary,
    WorkoutEventResult,
    WorkoutHistoryItem,
    WorkoutStatusResult,
)
from telegram.constants import ChatType

from app.main import (
    BACKEND_CLIENT_KEY,
    HELP_MESSAGE,
    REGISTRATION_ERROR_MESSAGE,
    _format_decimal,
    _format_workout_status,
    _split_telegram_message,
    cancel_workout,
    exercise_history,
    help_command,
    log_workout,
    open_workout,
    start,
    undo_workout,
    workout_status,
    workout_history,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("80"), "80"),
        (Decimal("70"), "70"),
        (Decimal("80.000"), "80"),
        (Decimal("8.500"), "8.5"),
        (Decimal("0.000"), "0"),
    ],
)
def test_format_decimal_only_trims_fractional_zeroes(
    value: Decimal, expected: str
) -> None:
    assert _format_decimal(value) == expected


def _objects(*, chat_type=ChatType.PRIVATE, user=True):
    message = SimpleNamespace(reply_text=AsyncMock(), text="panca 80x8")
    effective_user = (
        SimpleNamespace(
            id=12345,
            username="first_name",
            full_name="First User",
        )
        if user
        else None
    )
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type=chat_type),
        effective_user=effective_user,
        update_id=987,
    )
    backend = SimpleNamespace(
        register_telegram_user=AsyncMock(),
        process_workout_event=AsyncMock(),
        get_workout_status=AsyncMock(),
        query_history=AsyncMock(),
    )
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={BACKEND_CLIENT_KEY: backend}),
        args=[],
    )
    return update, context, message, backend


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("created", "expected"),
    [
        (True, "Registrazione completata ✅"),
        (False, "Bentornato! Il tuo profilo è già registrato."),
    ],
)
async def test_private_start_registers_and_replies(created: bool, expected: str) -> None:
    update, context, message, backend = _objects()
    backend.register_telegram_user.return_value = RegistrationResult(
        user_id=uuid4(), created=created
    )

    await start(update, context)

    backend.register_telegram_user.assert_awaited_once_with(
        telegram_user_id=12345,
        username="first_name",
        display_name="First User",
    )
    message.reply_text.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_group_start_redirects_to_private_chat() -> None:
    update, context, message, backend = _objects(chat_type=ChatType.GROUP)

    await start(update, context)

    backend.register_telegram_user.assert_not_awaited()
    message.reply_text.assert_awaited_once_with(
        "Per registrarti, apri una chat privata con il bot e invia /start."
    )


@pytest.mark.asyncio
async def test_start_without_effective_user_returns_safe_error() -> None:
    update, context, message, backend = _objects(user=False)

    await start(update, context)

    backend.register_telegram_user.assert_not_awaited()
    message.reply_text.assert_awaited_once_with(REGISTRATION_ERROR_MESSAGE)


@pytest.mark.asyncio
async def test_backend_failure_returns_safe_error() -> None:
    update, context, message, backend = _objects()
    backend.register_telegram_user.side_effect = BackendError("failed")

    await start(update, context)

    message.reply_text.assert_awaited_once_with(REGISTRATION_ERROR_MESSAGE)


@pytest.mark.asyncio
async def test_workout_command_passes_natural_date_and_formats_response() -> None:
    update, context, message, backend = _objects()
    context.args = ["ieri"]
    backend.process_workout_event.return_value = WorkoutEventResult(
        kind="opened",
        replayed=False,
        performed_on=date(2026, 8, 4),
        exercises=(),
        total_exercises=0,
        total_sets=0,
        clarification_message=None,
    )

    await open_workout(update, context)

    backend.process_workout_event.assert_awaited_once_with(
        telegram_user_id=12345,
        update_id=987,
        action="open",
        text="ieri",
    )
    message.reply_text.assert_awaited_once_with("Workout aperto per il 04/08/2026 ✅")


@pytest.mark.asyncio
async def test_text_message_formats_persisted_exercises() -> None:
    update, context, message, backend = _objects()
    backend.process_workout_event.return_value = WorkoutEventResult(
        kind="logged",
        replayed=False,
        performed_on=date(2026, 8, 5),
        exercises=(
            ExerciseSummary(
                name="Bench Press",
                sets=(SetSummary(8, Decimal("80.000"), "kg"),),
            ),
        ),
        total_exercises=1,
        total_sets=1,
        clarification_message=None,
    )

    await log_workout(update, context)

    backend.process_workout_event.assert_awaited_once_with(
        telegram_user_id=12345,
        update_id=987,
        action="log",
        text="panca 80x8",
    )
    message.reply_text.assert_awaited_once_with("Ho registrato:\nBench Press\n80 kg × 8")


@pytest.mark.asyncio
async def test_missing_draft_has_actionable_message() -> None:
    update, context, message, backend = _objects()
    backend.process_workout_event.side_effect = BackendError(
        "missing", code="noactiveworkout"
    )

    await log_workout(update, context)

    message.reply_text.assert_awaited_once_with(
        "Non hai un workout aperto. Usa /workout per iniziare."
    )


@pytest.mark.asyncio
async def test_active_workout_points_to_end_command() -> None:
    update, context, message, backend = _objects()
    backend.process_workout_event.side_effect = BackendError(
        "active", code="active_workout_exists"
    )

    await open_workout(update, context)

    message.reply_text.assert_awaited_once_with(
        "Hai gia un workout aperto. Usa /end per completarlo."
    )


@pytest.mark.asyncio
async def test_cancel_sends_event_and_confirms_permanent_deletion() -> None:
    update, context, message, backend = _objects()
    backend.process_workout_event.return_value = WorkoutEventResult(
        kind="cancelled",
        replayed=False,
        performed_on=None,
        exercises=(),
        total_exercises=0,
        total_sets=0,
        clarification_message=None,
    )

    await cancel_workout(update, context)

    backend.process_workout_event.assert_awaited_once_with(
        telegram_user_id=12345,
        update_id=987,
        action="cancel",
        text=None,
    )
    message.reply_text.assert_awaited_once_with("Workout eliminato.")


@pytest.mark.asyncio
async def test_undo_formats_the_removed_message_batch() -> None:
    update, context, message, backend = _objects()
    removed = ExerciseSummary(
        name="Bench Press",
        sets=(SetSummary(8, Decimal("80"), "kg"),),
    )
    backend.process_workout_event.return_value = WorkoutEventResult(
        kind="undone",
        replayed=False,
        performed_on=date(2026, 8, 5),
        exercises=(),
        removed_exercises=(removed,),
        total_exercises=1,
        total_sets=3,
        clarification_message=None,
    )

    await undo_workout(update, context)

    backend.process_workout_event.assert_awaited_once_with(
        telegram_user_id=12345,
        update_id=987,
        action="undo",
        text=None,
    )
    message.reply_text.assert_awaited_once_with(
        "Ho annullato:\nBench Press\n80 kg × 8\n\n"
        "Nel workout restano 1 esercizi e 3 serie."
    )


@pytest.mark.asyncio
async def test_help_is_local_and_does_not_require_registration() -> None:
    update, context, message, backend = _objects(user=False)

    await help_command(update, context)

    backend.register_telegram_user.assert_not_awaited()
    backend.process_workout_event.assert_not_awaited()
    message.reply_text.assert_awaited_once_with(HELP_MESSAGE)


@pytest.mark.asyncio
async def test_status_formats_complete_active_draft() -> None:
    update, context, message, backend = _objects()
    backend.get_workout_status.return_value = WorkoutStatusResult(
        kind="active",
        workout=WorkoutHistoryItem(
            performed_on=date(2026, 8, 5),
            notes="Push day",
            exercises=(
                ExerciseSummary(
                    name="Bench Press",
                    sets=(SetSummary(8, Decimal("80"), "kg"),),
                ),
            ),
        ),
    )

    await workout_status(update, context)

    backend.get_workout_status.assert_awaited_once_with(telegram_user_id=12345)
    message.reply_text.assert_awaited_once_with(
        _format_workout_status(backend.get_workout_status.return_value)
    )


@pytest.mark.asyncio
async def test_history_command_passes_limit_and_formats_complete_workout() -> None:
    update, context, message, backend = _objects()
    context.args = ["2"]
    backend.query_history.return_value = HistoryQueryResult(
        kind="workouts",
        workouts=(
            WorkoutHistoryItem(
                performed_on=date(2026, 8, 5),
                notes="Push day",
                exercises=(
                    ExerciseSummary(
                        name="Bench Press",
                        notes="Pausa al petto",
                        sets=(
                            SetSummary(
                                8,
                                Decimal("80.000"),
                                "kg",
                                "RPE 8",
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )

    await workout_history(update, context)

    backend.query_history.assert_awaited_once_with(
        telegram_user_id=12345,
        kind="workouts",
        query=None,
        limit=2,
    )
    message.reply_text.assert_awaited_once_with(
        "Storico workout\n\n"
        "05/08/2026\n"
        "Note workout: Push day\n"
        "Bench Press\n"
        "Note esercizio: Pausa al petto\n"
        "1. 80 kg × 8 — RPE 8"
    )


@pytest.mark.asyncio
async def test_exercise_command_parses_trailing_limit_and_empty_history() -> None:
    update, context, message, backend = _objects()
    context.args = ["panca", "piana", "7"]
    backend.query_history.return_value = HistoryQueryResult(
        kind="exercise",
        exercise_name="Panca piana",
        exercise_workouts=(),
    )

    await exercise_history(update, context)

    backend.query_history.assert_awaited_once_with(
        telegram_user_id=12345,
        kind="exercise",
        query="panca piana",
        limit=7,
    )
    message.reply_text.assert_awaited_once_with(
        "Nessun workout completato contiene Panca piana."
    )


@pytest.mark.asyncio
async def test_exercise_command_returns_clarification() -> None:
    update, context, message, backend = _objects()
    context.args = ["panca"]
    backend.query_history.return_value = HistoryQueryResult(
        kind="needs_clarification",
        clarification_message="Panca piana o inclinata?",
    )

    await exercise_history(update, context)

    message.reply_text.assert_awaited_once_with("Panca piana o inclinata?")


@pytest.mark.asyncio
async def test_history_requires_valid_limit_without_calling_backend() -> None:
    update, context, message, backend = _objects()
    context.args = ["21"]

    await workout_history(update, context)

    backend.query_history.assert_not_awaited()
    message.reply_text.assert_awaited_once_with("Uso: /history [limite da 1 a 20]")


def test_long_history_messages_are_split_without_data_loss() -> None:
    value = "Intestazione\n" + "nota " * 1200
    chunks = _split_telegram_message(value, limit=100)

    assert all(0 < len(chunk) <= 100 for chunk in chunks)
    assert "".join(chunks) == value
