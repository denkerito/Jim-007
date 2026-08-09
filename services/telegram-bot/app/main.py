"""Telegram application bootstrap."""

import logging

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.backend import BackendClient
from app.config import get_settings
from app.handlers import (
    BACKEND_CLIENT_KEY,
    cancel_workout,
    complete_workout,
    exercise_history,
    help_command,
    log_workout,
    open_workout,
    start,
    undo_workout,
    workout_history,
    workout_status,
    new_program,
    create_program,
    edit_program,
)


async def _close_backend(application: Application) -> None:
    backend = application.bot_data.get(BACKEND_CLIENT_KEY)
    if isinstance(backend, BackendClient):
        await backend.close()


async def _set_bot_commands(application: Application) -> None:
    await application.bot.set_my_commands(
        (
            BotCommand("start", "Verifica il collegamento web"),
            BotCommand("workout", "Apri un workout"),
            BotCommand("status", "Mostra il workout aperto"),
            BotCommand("undo", "Annulla l'ultimo inserimento"),
            BotCommand("end", "Completa il workout"),
            BotCommand("cancel", "Elimina il workout aperto"),
            BotCommand("history", "Mostra lo storico workout"),
            BotCommand("exercise", "Mostra lo storico esercizio"),
            BotCommand("newprogram", "Inizia un nuovo programma"),
            BotCommand("program", "Salva una giornata programmata"),
            BotCommand("editprogram", "Riscrive una giornata programmata"),
            BotCommand("help", "Mostra i comandi disponibili"),
        )
    )


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
        .post_init(_set_bot_commands)
        .post_shutdown(_close_backend)
        .build()
    )
    application.bot_data[BACKEND_CLIENT_KEY] = backend
    application.bot_data["public_web_url"] = settings.public_web_url
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("workout", open_workout))
    application.add_handler(CommandHandler("status", workout_status))
    application.add_handler(CommandHandler("undo", undo_workout))
    application.add_handler(CommandHandler("end", complete_workout))
    application.add_handler(CommandHandler("cancel", cancel_workout))
    application.add_handler(CommandHandler("history", workout_history))
    application.add_handler(CommandHandler("exercise", exercise_history))
    application.add_handler(CommandHandler("newprogram", new_program))
    application.add_handler(CommandHandler("program", create_program))
    application.add_handler(CommandHandler("editprogram", edit_program))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_workout))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
