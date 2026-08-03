from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from telegram.constants import ChatType

from app.backend import BackendError, RegistrationResult
from app.main import BACKEND_CLIENT_KEY, REGISTRATION_ERROR_MESSAGE, start


def _objects(*, chat_type=ChatType.PRIVATE, user=True):
    message = SimpleNamespace(reply_text=AsyncMock())
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
    backend = SimpleNamespace(register_telegram_user=AsyncMock())
    context = SimpleNamespace(
        application=SimpleNamespace(bot_data={BACKEND_CLIENT_KEY: backend})
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
