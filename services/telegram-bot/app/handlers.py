"""Telegram command and message handlers."""

import logging
from typing import Literal, cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from app.backend import BackendClient, BackendError
from app.presentation import (
    _format_history_result,
    _format_workout_result,
    _format_workout_status,
    _split_telegram_message,
    _format_program_event,
)


logger = logging.getLogger(__name__)
BACKEND_CLIENT_KEY = "backend_client"
REGISTRATION_ERROR_MESSAGE = "Non riesco a verificare il collegamento in questo momento. Riprova tra poco."
TELEGRAM_NOT_LINKED_MESSAGE = (
    "Questo account Telegram non è ancora collegato a JIM007. "
    "Accedi alla web app e scegli ‘Collega Telegram’."
)
WORKOUT_ERROR_MESSAGE = (
    "Non riesco a elaborare il workout in questo momento. Riprova tra poco."
)
HISTORY_ERROR_MESSAGE = (
    "Non riesco a recuperare lo storico in questo momento. Riprova tra poco."
)
HELP_MESSAGE = (
    "Comandi disponibili:\n"
    "/start - verifica il collegamento con la web app\n"
    "/workout [data] - apre un workout, per esempio /workout ieri\n"
    "Invia un messaggio testuale per registrare esercizi e serie\n"
    "/status - mostra il workout aperto\n"
    "/undo - annulla l'ultimo messaggio registrato\n"
    "/end - completa il workout\n"
    "/cancel - elimina il workout aperto\n"
    "/history [limite] - mostra gli ultimi workout\n"
    "/exercise <nome> [limite] - mostra lo storico di un esercizio\n"
    "/newprogram - disattiva le giornate programmate correnti\n"
    "/program <numero> <alias>, <scheda>[, note] - salva una giornata\n"
    "/editprogram <numero|alias>, <scheda>[, note] - riscrive una giornata\n"
    "/help - mostra questo messaggio"
)
PROGRAM_ERROR_MESSAGE = "Non riesco a modificare il programma in questo momento. Riprova tra poco."


def _backend(context: ContextTypes.DEFAULT_TYPE) -> BackendClient | None:
    value = context.application.bot_data.get(BACKEND_CLIENT_KEY)
    return cast(BackendClient, value) if value is not None else None


def _private_chat_parts(update: Update):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user
    if message is None or chat is None or user is None:
        return None
    if chat.type != ChatType.PRIVATE:
        return None
    return message, user


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return

    chat = update.effective_chat
    if chat is None or chat.type != ChatType.PRIVATE:
        await message.reply_text(
            "Per collegare Telegram, apri una chat privata con il bot."
        )
        return

    user = update.effective_user
    if user is None:
        await message.reply_text(REGISTRATION_ERROR_MESSAGE)
        return

    backend = _backend(context)
    if backend is None:
        logger.error("Backend client is not configured")
        await message.reply_text(REGISTRATION_ERROR_MESSAGE)
        return

    payload = context.args[0] if len(context.args) == 1 else None
    try:
        if payload is not None and payload.startswith("link_"):
            token = payload.removeprefix("link_")
            if not token:
                raise BackendError("Invalid Telegram link", code="telegram_link_invalid")
            result = await backend.claim_telegram_link(
                token=token, telegram_user_id=user.id,
                update_id=update.update_id, username=user.username,
                display_name=user.full_name,
            )
        elif payload is None:
            result = await backend.resolve_telegram_connection(
                telegram_user_id=user.id, username=user.username,
                display_name=user.full_name,
            )
        else:
            raise BackendError("Invalid Telegram link", code="telegram_link_invalid")
    except BackendError as error:
        logger.warning(
            "Telegram linking failed for update_id=%s",
            update.update_id,
            exc_info=True,
        )
        if error.code == "telegram_link_invalid":
            await message.reply_text(
                "Questo link non è valido o è scaduto. Generane uno nuovo dalla web app.",
                reply_markup=_web_cta(context),
            )
        else:
            await message.reply_text(REGISTRATION_ERROR_MESSAGE)
        return

    if result.kind == "candidate_recorded":
        await message.reply_text(
            "Account Telegram riconosciuto ✅\nTorna nella web app e conferma il collegamento."
        )
    elif result.kind == "linked":
        await message.reply_text("Account collegato. Bentornato! Usa /workout per iniziare.")
    else:
        await _reply_not_linked(message, context)


def _web_cta(context: ContextTypes.DEFAULT_TYPE) -> InlineKeyboardMarkup:
    url = context.application.bot_data.get("public_web_url", "http://localhost:3000")
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("Apri JIM007", url=f"{str(url).rstrip('/')}/account")]]
    )


async def _reply_not_linked(message, context: ContextTypes.DEFAULT_TYPE) -> None:
    await message.reply_text(TELEGRAM_NOT_LINKED_MESSAGE, reply_markup=_web_cta(context))


def _private_event_parts(update: Update):
    parts = _private_chat_parts(update)
    if parts is None or update.update_id is None:
        return None
    message, user = parts
    return message, user, update.update_id


async def _send_workout_event(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    action: Literal["open", "log", "complete", "cancel", "undo"],
    text: str | None,
) -> None:
    parts = _private_event_parts(update)
    if parts is None:
        message = update.effective_message
        if message is not None:
            await message.reply_text("Per registrare workout, usa una chat privata con il bot.")
        return
    message, user, update_id = parts
    backend = _backend(context)
    if backend is None:
        logger.error("Backend client is not configured")
        await message.reply_text(WORKOUT_ERROR_MESSAGE)
        return
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
            "telegram_not_linked": TELEGRAM_NOT_LINKED_MESSAGE,
            "noactiveworkout": "Non hai un workout aperto. Usa /workout per iniziare.",
            "active_workout_exists": "Hai gia un workout aperto. Usa /end per completarlo.",
            "invalidworkoutdate": "La data del workout non puo essere futura.",
            "nothingtoundo": "Non ci sono inserimenti da annullare in questo workout.",
        }
        if error.code == "telegram_not_linked":
            await _reply_not_linked(message, context)
        else:
            await message.reply_text(messages.get(error.code, WORKOUT_ERROR_MESSAGE))
        return
    await message.reply_text(_format_workout_result(result))


async def open_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = " ".join(context.args).strip() or None
    await _send_workout_event(update, context, action="open", text=text)


async def complete_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_workout_event(update, context, action="complete", text=None)


async def cancel_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_workout_event(update, context, action="cancel", text=None)


async def undo_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_workout_event(update, context, action="undo", text=None)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    message = update.effective_message
    if message is not None:
        await message.reply_text(HELP_MESSAGE)


def _raw_command_payload(text: str | None) -> str:
    if not text:
        return ""
    return text.partition(" ")[2].strip()


async def _send_program_event(
    update: Update, context: ContextTypes.DEFAULT_TYPE, *, action: Literal["new", "create", "edit"],
    day_number: int | None = None, alias: str | None = None,
    selector: str | None = None, text: str | None = None, notes: str | None = None,
) -> None:
    parts = _private_event_parts(update)
    if parts is None:
        message = update.effective_message
        if message is not None:
            await message.reply_text("Per gestire il programma, usa una chat privata con il bot.")
        return
    message, user, update_id = parts
    backend = _backend(context)
    if backend is None:
        await message.reply_text(PROGRAM_ERROR_MESSAGE)
        return
    try:
        result = await backend.process_program_event(
            telegram_user_id=user.id, update_id=update_id, action=action,
            day_number=day_number, alias=alias, selector=selector, text=text, notes=notes,
        )
    except BackendError as error:
        messages = {
            "telegram_not_linked": TELEGRAM_NOT_LINKED_MESSAGE,
            "programworkoutconflict": "Numero o alias gia in uso. Usa /editprogram per sostituire la giornata.",
            "not_found": "Non trovo una giornata attiva con questo numero o alias.",
        }
        if error.code == "telegram_not_linked":
            await _reply_not_linked(message, context)
        else:
            await message.reply_text(messages.get(error.code, PROGRAM_ERROR_MESSAGE))
        return
    for chunk in _split_telegram_message(_format_program_event(result)):
        await message.reply_text(chunk)


async def new_program(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    if _raw_command_payload(message.text):
        await message.reply_text("Uso: /newprogram")
        return
    await _send_program_event(update, context, action="new")


async def create_program(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    parts = _raw_command_payload(message.text).split(",", maxsplit=2)
    if len(parts) < 2:
        await message.reply_text("Uso: /program <numero> <alias>, <scheda>[, note]")
        return
    header = parts[0].strip().split(maxsplit=1)
    try:
        day_number = int(header[0])
    except (ValueError, IndexError):
        day_number = 0
    alias = header[1].strip() if len(header) == 2 else ""
    prescription = parts[1].strip()
    notes = parts[2].strip() or None if len(parts) == 3 else None
    if not 1 <= day_number <= 32767 or not alias or alias.isdecimal() or not prescription:
        await message.reply_text("Uso: /program <numero> <alias>, <scheda>[, note]")
        return
    await _send_program_event(
        update, context, action="create", day_number=day_number,
        alias=alias, text=prescription, notes=notes,
    )


async def edit_program(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    parts = _raw_command_payload(message.text).split(",", maxsplit=2)
    selector = parts[0].strip() if parts else ""
    prescription = parts[1].strip() if len(parts) >= 2 else ""
    notes = parts[2].strip() or None if len(parts) == 3 else None
    if not selector or not prescription:
        await message.reply_text("Uso: /editprogram <numero|alias>, <scheda>[, note]")
        return
    await _send_program_event(
        update, context, action="edit", selector=selector,
        text=prescription, notes=notes,
    )


async def workout_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    parts = _private_chat_parts(update)
    if parts is None:
        await message.reply_text(
            "Per consultare il workout aperto, usa una chat privata con il bot."
        )
        return
    _, user = parts
    backend = _backend(context)
    if backend is None:
        logger.error("Backend client is not configured")
        await message.reply_text(WORKOUT_ERROR_MESSAGE)
        return
    try:
        result = await backend.get_workout_status(telegram_user_id=user.id)
    except BackendError as error:
        logger.warning(
            "Workout status failed for update_id=%s",
            update.update_id,
            exc_info=True,
        )
        if error.code == "telegram_not_linked":
            await _reply_not_linked(message, context)
        else:
            await message.reply_text(WORKOUT_ERROR_MESSAGE)
        return
    for chunk in _split_telegram_message(_format_workout_status(result)):
        await message.reply_text(chunk)


async def log_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    await _send_workout_event(
        update,
        context,
        action="log",
        text=message.text if message is not None else None,
    )


def _history_limit(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if 1 <= parsed <= 20 else None


async def workout_history(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    if len(context.args) > 1:
        await message.reply_text("Uso: /history [limite da 1 a 20]")
        return
    limit = 5
    if context.args:
        parsed = _history_limit(context.args[0])
        if parsed is None:
            await message.reply_text("Uso: /history [limite da 1 a 20]")
            return
        limit = parsed
    await _send_history_query(
        update,
        context,
        kind="workouts",
        query=None,
        limit=limit,
    )


async def exercise_history(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    message = update.effective_message
    if message is None:
        return
    arguments = list(context.args)
    if not arguments:
        await message.reply_text("Uso: /exercise <nome> [limite da 1 a 20]")
        return

    limit = 5
    try:
        trailing_limit = int(arguments[-1])
    except ValueError:
        trailing_limit = None
    if trailing_limit is not None:
        if not 1 <= trailing_limit <= 20:
            await message.reply_text("Uso: /exercise <nome> [limite da 1 a 20]")
            return
        limit = trailing_limit
        arguments.pop()
    query = " ".join(arguments).strip()
    if not query:
        await message.reply_text("Uso: /exercise <nome> [limite da 1 a 20]")
        return
    await _send_history_query(
        update,
        context,
        kind="exercise",
        query=query,
        limit=limit,
    )


async def _send_history_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    kind: Literal["workouts", "exercise"],
    query: str | None,
    limit: int,
) -> None:
    message = update.effective_message
    if message is None:
        return
    parts = _private_chat_parts(update)
    if parts is None:
        await message.reply_text("Per consultare lo storico, usa una chat privata con il bot.")
        return
    _, user = parts

    backend = _backend(context)
    if backend is None:
        logger.error("Backend client is not configured")
        await message.reply_text(HISTORY_ERROR_MESSAGE)
        return
    try:
        result = await backend.query_history(
            telegram_user_id=user.id,
            kind=kind,
            query=query,
            limit=limit,
        )
    except BackendError as error:
        logger.warning(
            "History query failed for update_id=%s kind=%s",
            update.update_id,
            kind,
            exc_info=True,
        )
        if error.code == "telegram_not_linked":
            await _reply_not_linked(message, context)
        else:
            await message.reply_text(HISTORY_ERROR_MESSAGE)
        return

    for chunk in _split_telegram_message(_format_history_result(result)):
        await message.reply_text(chunk)
