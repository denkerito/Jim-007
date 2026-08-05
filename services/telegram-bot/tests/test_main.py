from types import SimpleNamespace
from unittest.mock import AsyncMock
from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest
from telegram.constants import ChatType

from app.backend import (
    BackendError,
    ExerciseSummary,
    RegistrationResult,
    SetSummary,
    WorkoutEventResult,
)
from app.main import (
    BACKEND_CLIENT_KEY,
    REGISTRATION_ERROR_MESSAGE,
    log_workout,
    open_workout,
    start,
)


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
