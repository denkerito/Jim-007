import logging
from typing import Literal, cast

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from app.backend import BackendClient, BackendError, ExerciseSummary, WorkoutEventResult
from app.config import get_settings


logger = logging.getLogger(__name__)
BACKEND_CLIENT_KEY = "backend_client"
REGISTRATION_ERROR_MESSAGE = (
    "Non riesco a completare la registrazione in questo momento. "
    "Riprova tra poco con /start."
)
WORKOUT_ERROR_MESSAGE = (
    "Non riesco a elaborare il workout in questo momento. Riprova tra poco."
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
    except BackendError as error:
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


def _private_event_parts(update: Update):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None or update.update_id is None:
        return None
    if chat.type != ChatType.PRIVATE:
        return None
    return message, user, update.update_id


async def _send_workout_event(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    action: Literal["open", "log", "complete"],
    text: str | None,
) -> None:
    parts = _private_event_parts(update)
    if parts is None:
        message = update.effective_message
        if message is not None:
            await message.reply_text("Per registrare workout, usa una chat privata con il bot.")
        return
    message, user, update_id = parts
    backend_value = context.application.bot_data.get(BACKEND_CLIENT_KEY)
    if backend_value is None:
        logger.error("Backend client is not configured")
        await message.reply_text(WORKOUT_ERROR_MESSAGE)
        return
    backend = cast(BackendClient, backend_value)
    try:
        result = await backend.process_workout_event(
            telegram_user_id=user.id,
            update_id=update_id,
            action=action,
            text=text,
        )
    except BackendError as error:
        logger.warning(
            "Workout event failed for update_id=%s action=%s",
            update_id,
            action,
            exc_info=True,
        )
        messages = {
            "external_identity_not_registered": "Registrati prima con /start.",
            "noactiveworkout": "Non hai un workout aperto. Usa /workout per iniziare.",
            "active_workout_exists": "Hai gia un workout aperto. Usa /fine per completarlo.",
            "invalidworkoutdate": "La data del workout non puo essere futura.",
        }
        await message.reply_text(messages.get(error.code, WORKOUT_ERROR_MESSAGE))
        return
    await message.reply_text(_format_workout_result(result))


async def open_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip() or None
    await _send_workout_event(update, context, action="open", text=text)


async def complete_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_workout_event(update, context, action="complete", text=None)


async def log_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    await _send_workout_event(
        update,
        context,
        action="log",
        text=message.text if message is not None else None,
    )


def _format_decimal(value) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _format_exercise(value: ExerciseSummary) -> str:
    lines = [value.name]
    for item in value.sets:
        if item.load_value is None:
            lines.append(f"{item.repetitions} ripetizioni")
        else:
            lines.append(
                f"{_format_decimal(item.load_value)} {item.load_unit} \u00d7 {item.repetitions}"
            )
    return "\n".join(lines)


def _format_workout_result(result: WorkoutEventResult) -> str:
    if result.kind == "needs_clarification":
        return result.clarification_message or "Puoi riformulare il messaggio?"
    if result.replayed:
        return "Questo aggiornamento era gia stato elaborato."
    if result.kind == "opened":
        if result.performed_on is None:
            return "Workout aperto \u2705"
        return f"Workout aperto per il {result.performed_on.strftime('%d/%m/%Y')} \u2705"
    if result.kind == "completed":
        return (
            "Workout completato \u2705\n"
            f"{result.total_exercises} esercizi, {result.total_sets} serie."
        )
    rendered = "\n\n".join(_format_exercise(item) for item in result.exercises)
    return f"Ho registrato:\n{rendered}"


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
    application.add_handler(CommandHandler("workout", open_workout))
    application.add_handler(CommandHandler("fine", complete_workout))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, log_workout))
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
