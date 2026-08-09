"""Read-only workout and exercise history use cases."""

from __future__ import annotations

import base64
import binascii
from uuid import UUID

from pydantic import ValidationError

from app.application.commands import (
    ExerciseCatalogItem,
    ExerciseHistoryItem,
    ExerciseHistoryPage,
    ExerciseResolutionStatus,
    HistoryCursor,
    HistoryQueryKind,
    HistoryQueryResult,
    ProcessHistoryQueryCommand,
    WorkoutHistoryPage,
)
from app.application.ports import (
    ExerciseQueryInterpreter,
    UnitOfWorkFactory,
)
from app.domain.exceptions import (
    TelegramNotLinkedError,
    InvalidHistoryCursorError,
    LlmInvalidResponseError,
    NotFoundError,
)
from app.domain.models import Workout
from app.domain.normalization import clean_required_text, normalize_exercise_name


def _decode_cursor(value: str | None) -> HistoryCursor | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        raw = base64.b64decode(
            value + padding,
            altchars=b"-_",
            validate=True,
        )
        return HistoryCursor.model_validate_json(raw)
    except (binascii.Error, UnicodeError, ValidationError, ValueError) as error:
        raise InvalidHistoryCursorError("The history cursor is invalid") from error


def _encode_cursor(cursor: HistoryCursor) -> str:
    encoded = base64.urlsafe_b64encode(cursor.model_dump_json().encode("utf-8"))
    return encoded.decode("ascii").rstrip("=")


def _workout_cursor(workout: Workout) -> str:
    return _encode_cursor(
        HistoryCursor(
            performed_on=workout.performed_on,
            created_at=workout.created_at,
            workout_id=workout.id,
        )
    )


def _exercise_item_cursor(item: ExerciseHistoryItem) -> str:
    return _encode_cursor(
        HistoryCursor(
            performed_on=item.performed_on,
            created_at=item.workout_created_at,
            workout_id=item.workout_id,
        )
    )


class ListWorkoutHistory:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        *,
        user_id: UUID,
        limit: int = 5,
        cursor: str | None = None,
    ) -> WorkoutHistoryPage:
        after = _decode_cursor(cursor)
        async with self._uow_factory() as uow:
            if await uow.users.get_by_id(user_id) is None:
                raise NotFoundError("User not found")
            values = await uow.workouts.list_completed(
                user_id,
                limit=limit + 1,
                after=after,
            )
        items = values[:limit]
        next_cursor = _workout_cursor(items[-1]) if len(values) > limit else None
        return WorkoutHistoryPage(items=items, next_cursor=next_cursor)


class ListExerciseHistory:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        *,
        user_id: UUID,
        exercise_id: UUID,
        limit: int = 5,
        cursor: str | None = None,
    ) -> ExerciseHistoryPage:
        after = _decode_cursor(cursor)
        async with self._uow_factory() as uow:
            if await uow.users.get_by_id(user_id) is None:
                raise NotFoundError("User not found")
            exercise = await uow.exercises.get_by_id(exercise_id, user_id)
            if exercise is None:
                raise NotFoundError("Exercise not found")
            values = await uow.workouts.list_completed_for_exercise(
                user_id,
                exercise_id,
                limit=limit + 1,
                after=after,
            )
        items = values[:limit]
        next_cursor = (
            _exercise_item_cursor(items[-1]) if len(values) > limit else None
        )
        return ExerciseHistoryPage(
            exercise=exercise,
            items=items,
            next_cursor=next_cursor,
        )


class ProcessHistoryQuery:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        interpreter: ExerciseQueryInterpreter,
    ) -> None:
        self._uow_factory = uow_factory
        self._interpreter = interpreter

    async def execute(self, command: ProcessHistoryQueryCommand) -> HistoryQueryResult:
        async with self._uow_factory() as uow:
            identity = await uow.external_identities.get_by_provider_subject(
                command.provider.strip(),
                command.provider_subject.strip(),
            )
            if identity is None:
                raise TelegramNotLinkedError(
                    "Telegram is not linked to a web account"
                )
            user = await uow.users.get_by_id(identity.user_id)
            if user is None:
                raise RuntimeError("External identity references a missing user")

            if command.kind is HistoryQueryKind.WORKOUTS:
                exact_exercise = None
                catalog_values = ()
            else:
                query = clean_required_text(command.query or "")
                exact_exercise = await uow.exercises.get_by_normalized_name(
                    user.id,
                    normalize_exercise_name(query),
                )
                catalog_values = (
                    ()
                    if exact_exercise is not None
                    else await uow.exercises.list_for_user(user.id)
                )

        if command.kind is HistoryQueryKind.WORKOUTS:
            page = await ListWorkoutHistory(self._uow_factory).execute(
                user_id=user.id,
                limit=command.limit,
                cursor=command.cursor,
            )
            return HistoryQueryResult(kind="workouts", workout_history=page)

        if exact_exercise is not None:
            exercise_id = exact_exercise.id
        else:
            if not catalog_values:
                return HistoryQueryResult(kind="exercise_not_found")
            catalog = tuple(
                ExerciseCatalogItem(id=exercise.id, name=exercise.name)
                for exercise in catalog_values
            )
            interpretation = await self._interpreter.resolve_exercise(
                text=clean_required_text(command.query or ""),
                locale=user.locale,
                catalog=catalog,
            )
            if interpretation.status is ExerciseResolutionStatus.NOT_FOUND:
                return HistoryQueryResult(kind="exercise_not_found")
            if interpretation.status is ExerciseResolutionStatus.NEEDS_CLARIFICATION:
                return HistoryQueryResult(
                    kind="needs_clarification",
                    clarification_message=interpretation.clarification_message,
                )
            exercise_id = interpretation.exercise_id
            if exercise_id is None or all(
                item.id != exercise_id for item in catalog_values
            ):
                raise LlmInvalidResponseError(
                    "The exercise resolver selected an ID outside the user catalog"
                )

        page = await ListExerciseHistory(self._uow_factory).execute(
            user_id=user.id,
            exercise_id=exercise_id,
            limit=command.limit,
            cursor=command.cursor,
        )
        return HistoryQueryResult(kind="exercise", exercise_history=page)
