import logging

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.config import get_settings


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if update.message is not None:
        await update.message.reply_text("JIM007 è online. Il dominio workout non è ancora implementato.")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level.upper())
    # Telegram embeds the bot token in request URLs; do not log HTTP requests.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if settings.telegram_mode != "polling":
        raise RuntimeError("La modalità webhook non è ancora configurata")

    application = Application.builder().token(settings.telegram_bot_token.get_secret_value()).build()
    application.add_handler(CommandHandler("start", start))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
