"""Pure Telegram message formatting helpers."""

from decimal import Decimal

from app.backend import (
    ExerciseHistoryWorkout,
    ExerciseSummary,
    HistoryQueryResult,
    WorkoutEventResult,
    WorkoutHistoryItem,
    WorkoutStatusResult,
)


TELEGRAM_MESSAGE_LIMIT = 4096


def _format_decimal(value: Decimal) -> str:
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
                f"{_format_decimal(item.load_value)} {item.load_unit} × {item.repetitions}"
            )
    return "\n".join(lines)


def _format_workout_result(result: WorkoutEventResult) -> str:
    if result.kind == "needs_clarification":
        return result.clarification_message or "Puoi riformulare il messaggio?"
    if result.replayed:
        return "Questo aggiornamento era gia stato elaborato."
    if result.kind == "opened":
        if result.performed_on is None:
            return "Workout aperto ✅"
        return f"Workout aperto per il {result.performed_on.strftime('%d/%m/%Y')} ✅"
    if result.kind == "completed":
        return (
            "Workout completato ✅\n"
            f"{result.total_exercises} esercizi, {result.total_sets} serie."
        )
    if result.kind == "cancelled":
        return "Workout eliminato."
    if result.kind == "undone":
        rendered = "\n\n".join(
            _format_exercise(item) for item in result.removed_exercises
        )
        return (
            "Ho annullato:\n"
            f"{rendered}\n\n"
            f"Nel workout restano {result.total_exercises} esercizi e "
            f"{result.total_sets} serie."
        )
    rendered = "\n\n".join(_format_exercise(item) for item in result.exercises)
    return f"Ho registrato:\n{rendered}"


def _format_workout_status(result: WorkoutStatusResult) -> str:
    if result.kind == "none" or result.workout is None:
        return "Non hai un workout aperto. Usa /workout per iniziare."
    workout = result.workout
    lines = [f"Workout aperto del {workout.performed_on.strftime('%d/%m/%Y')}"]
    if workout.notes:
        lines.append(f"Note workout: {workout.notes}")
    if not workout.exercises:
        lines.append("Nessun esercizio registrato.")
    else:
        lines.append(
            "\n\n".join(_format_history_exercise(item) for item in workout.exercises)
        )
    return "\n\n".join(lines)


def _format_history_exercise(value: ExerciseSummary) -> str:
    lines = [value.name]
    if value.notes:
        lines.append(f"Note esercizio: {value.notes}")
    for number, item in enumerate(value.sets, start=1):
        if item.load_value is None:
            rendered = f"{number}. {item.repetitions} ripetizioni"
        else:
            rendered = (
                f"{number}. {_format_decimal(item.load_value)} {item.load_unit} "
                f"× {item.repetitions}"
            )
        if item.notes:
            rendered += f" — {item.notes}"
        lines.append(rendered)
    return "\n".join(lines)


def _format_workout_history_item(value: WorkoutHistoryItem) -> str:
    lines = [value.performed_on.strftime("%d/%m/%Y")]
    if value.notes:
        lines.append(f"Note workout: {value.notes}")
    lines.append("\n\n".join(_format_history_exercise(item) for item in value.exercises))
    return "\n".join(lines)


def _format_exercise_history_item(value: ExerciseHistoryWorkout) -> str:
    lines = [value.performed_on.strftime("%d/%m/%Y")]
    if value.workout_notes:
        lines.append(f"Note workout: {value.workout_notes}")
    lines.append(
        "\n\n".join(_format_history_exercise(item) for item in value.occurrences)
    )
    return "\n".join(lines)


def _format_history_result(result: HistoryQueryResult) -> str:
    if result.kind == "exercise_not_found":
        return "Non trovo un esercizio corrispondente nel tuo catalogo."
    if result.kind == "needs_clarification":
        return result.clarification_message or "Quale esercizio intendi?"
    if result.kind == "workouts":
        if not result.workouts:
            return "Non hai ancora workout completati."
        rendered = "\n\n———\n\n".join(
            _format_workout_history_item(item) for item in result.workouts
        )
        return f"Storico workout\n\n{rendered}"
    if not result.exercise_workouts:
        return (
            f"Nessun workout completato contiene {result.exercise_name or 'questo esercizio'}."
        )
    rendered = "\n\n———\n\n".join(
        _format_exercise_history_item(item) for item in result.exercise_workouts
    )
    return f"Storico {result.exercise_name}\n\n{rendered}"


def _split_telegram_message(
    value: str, *, limit: int = TELEGRAM_MESSAGE_LIMIT
) -> tuple[str, ...]:
    chunks: list[str] = []
    remaining = value
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = limit
        else:
            split_at += 1
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]
    if remaining:
        chunks.append(remaining)
    return tuple(chunks)
