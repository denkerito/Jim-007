import logging
from typing import cast

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes

from app.backend import BackendClient, BackendError
from app.config import get_settings


logger = logging.getLogger(__name__)
BACKEND_CLIENT_KEY = "backend_client"
REGISTRATION_ERROR_MESSAGE = (
    "Non riesco a completare la registrazione in questo momento. "
    "Riprova tra poco con /start."
)


async def _close_backend(application: Application) -> None:
    backend = application.bot_data.get(BACKEND_CLIENT_KEY)
    if isinstance(backend, BackendClient):
        await backend.close()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    chat = update.effective_chat
    if chat is None or chat.type != ChatType.PRIVATE:
        await message.reply_text(
            "Per registrarti, apri una chat privata con il bot e invia /start."
        )
        return

    user = update.effective_user
    if user is None:
        await message.reply_text(REGISTRATION_ERROR_MESSAGE)
        return

    backend_value = context.application.bot_data.get(BACKEND_CLIENT_KEY)
    if backend_value is None:
        logger.error("Backend client is not configured")
        await message.reply_text(REGISTRATION_ERROR_MESSAGE)
        return
    backend = cast(BackendClient, backend_value)

    try:
        result = await backend.register_telegram_user(
            telegram_user_id=user.id,
            username=user.username,
            display_name=user.full_name,
        )
    except BackendError:
        logger.warning(
            "Telegram registration failed for update_id=%s",
            update.update_id,
            exc_info=True,
        )
        await message.reply_text(REGISTRATION_ERROR_MESSAGE)
        return

    if result.created:
        await message.reply_text("Registrazione completata ✅")
    else:
        await message.reply_text("Bentornato! Il tuo profilo è già registrato.")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    # Telegram embeds the bot token in request URLs; do not log HTTP requests.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if settings.telegram_mode != "polling":
        raise RuntimeError("La modalità webhook non è ancora configurata")

    backend = BackendClient(
        base_url=settings.backend_base_url,
        internal_api_token=settings.internal_api_token.get_secret_value(),
    )
    application = (
        Application.builder()
        .token(settings.telegram_bot_token.get_secret_value())
        .post_shutdown(_close_backend)
        .build()
    )
    application.bot_data[BACKEND_CLIENT_KEY] = backend
    application.add_handler(CommandHandler("start", start))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
