"""HTTP client for the internal JIM007 API."""

import asyncio
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError


class BackendError(RuntimeError):
    """Raised when a backend request cannot be completed or validated."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class _TelegramInternalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    kind: Literal["linked", "unlinked", "candidate_recorded"]
    user_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class TelegramConnectionResult:
    kind: Literal["linked", "unlinked", "candidate_recorded"]


class _LoadResponse(BaseModel):
    value: Decimal
    unit: str


class _PerformedSetResponse(BaseModel):
    set_number: int = 1
    repetitions: int
    load: _LoadResponse | None = None
    notes: str | None = None


class _ExerciseResponse(BaseModel):
    name: str


class _WorkoutExerciseResponse(BaseModel):
    exercise: _ExerciseResponse
    notes: str | None = None
    sets: tuple[_PerformedSetResponse, ...]


class _ProgramWorkoutItemResponse(BaseModel):
    id: UUID
    position: int
    exercise_name: str
    exercise_id: UUID | None = None
    target_sets: int
    target_repetitions: int
    rest_seconds: int | None = None


class _ProgramWorkoutResponse(BaseModel):
    id: UUID
    day_number: int
    alias: str
    notes: str | None = None
    active: bool = True
    items: tuple[_ProgramWorkoutItemResponse, ...]


class _WorkoutResponse(BaseModel):
    performed_on: date
    notes: str | None = None
    exercises: tuple[_WorkoutExerciseResponse, ...]
    program_workout: _ProgramWorkoutResponse | None = None


class _ProgramExerciseHistoryResponse(BaseModel):
    item: _ProgramWorkoutItemResponse
    latest_performed_on: date | None = None
    latest_workout_notes: str | None = None
    latest_occurrences: tuple[_WorkoutExerciseResponse, ...] = ()


class _WorkoutEventResponse(BaseModel):
    kind: Literal[
        "opened",
        "logged",
        "completed",
        "cancelled",
        "undone",
        "needs_clarification",
    ]
    replayed: bool = False
    workout: _WorkoutResponse | None = None
    added_exercises: tuple[_WorkoutExerciseResponse, ...] = ()
    removed_exercises: tuple[_WorkoutExerciseResponse, ...] = ()
    clarification_message: str | None = None
    program_history: tuple[_ProgramExerciseHistoryResponse, ...] = ()


class _ProgramEventResponse(BaseModel):
    kind: Literal["reset", "created", "edited", "needs_clarification"]
    replayed: bool = False
    deactivated_count: int = 0
    program_workout: _ProgramWorkoutResponse | None = None
    clarification_message: str | None = None


class _NoActiveWorkoutStatusResponse(BaseModel):
    kind: Literal["none"]


class _ActiveWorkoutStatusResponse(BaseModel):
    kind: Literal["active"]
    workout: _WorkoutResponse


_WorkoutStatusResponse = Annotated[
    _NoActiveWorkoutStatusResponse | _ActiveWorkoutStatusResponse,
    Field(discriminator="kind"),
]
_workout_status_adapter = TypeAdapter(_WorkoutStatusResponse)


class _ExerciseHistoryItemResponse(BaseModel):
    performed_on: date
    workout_notes: str | None = None
    occurrences: tuple[_WorkoutExerciseResponse, ...]


class _WorkoutHistoryQueryResponse(BaseModel):
    kind: Literal["workouts"]
    items: tuple[_WorkoutResponse, ...]


class _ExerciseHistoryQueryResponse(BaseModel):
    kind: Literal["exercise"]
    exercise: _ExerciseResponse
    items: tuple[_ExerciseHistoryItemResponse, ...]


class _ExerciseNotFoundQueryResponse(BaseModel):
    kind: Literal["exercise_not_found"]


class _HistoryClarificationQueryResponse(BaseModel):
    kind: Literal["needs_clarification"]
    clarification_message: str


_HistoryQueryResponse = Annotated[
    _WorkoutHistoryQueryResponse
    | _ExerciseHistoryQueryResponse
    | _ExerciseNotFoundQueryResponse
    | _HistoryClarificationQueryResponse,
    Field(discriminator="kind"),
]
_history_query_adapter = TypeAdapter(_HistoryQueryResponse)


@dataclass(frozen=True, slots=True)
class SetSummary:
    repetitions: int
    load_value: Decimal | None
    load_unit: str | None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class ExerciseSummary:
    name: str
    sets: tuple[SetSummary, ...]
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class PlannedExerciseSummary:
    name: str
    target_sets: int
    target_repetitions: int
    rest_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ProgramWorkoutSummary:
    day_number: int
    alias: str
    notes: str | None
    items: tuple[PlannedExerciseSummary, ...]


@dataclass(frozen=True, slots=True)
class PreviousExerciseSummary:
    planned: PlannedExerciseSummary
    performed_on: date | None = None
    workout_notes: str | None = None
    occurrences: tuple[ExerciseSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class WorkoutEventResult:
    kind: Literal[
        "opened",
        "logged",
        "completed",
        "cancelled",
        "undone",
        "needs_clarification",
    ]
    replayed: bool
    performed_on: date | None
    exercises: tuple[ExerciseSummary, ...]
    total_exercises: int
    total_sets: int
    clarification_message: str | None
    removed_exercises: tuple[ExerciseSummary, ...] = ()
    program_workout: ProgramWorkoutSummary | None = None
    program_history: tuple[PreviousExerciseSummary, ...] = ()


@dataclass(frozen=True, slots=True)
class ProgramEventResult:
    kind: Literal["reset", "created", "edited", "needs_clarification"]
    replayed: bool = False
    deactivated_count: int = 0
    program_workout: ProgramWorkoutSummary | None = None
    clarification_message: str | None = None


@dataclass(frozen=True, slots=True)
class WorkoutHistoryItem:
    performed_on: date
    notes: str | None
    exercises: tuple[ExerciseSummary, ...]
    program_workout: ProgramWorkoutSummary | None = None


@dataclass(frozen=True, slots=True)
class WorkoutStatusResult:
    kind: Literal["none", "active"]
    workout: WorkoutHistoryItem | None = None


@dataclass(frozen=True, slots=True)
class ExerciseHistoryWorkout:
    performed_on: date
    workout_notes: str | None
    occurrences: tuple[ExerciseSummary, ...]


@dataclass(frozen=True, slots=True)
class HistoryQueryResult:
    kind: Literal[
        "workouts",
        "exercise",
        "exercise_not_found",
        "needs_clarification",
    ]
    workouts: tuple[WorkoutHistoryItem, ...] = ()
    exercise_name: str | None = None
    exercise_workouts: tuple[ExerciseHistoryWorkout, ...] = ()
    clarification_message: str | None = None


class BackendClient:
    def __init__(
        self,
        *,
        base_url: str,
        internal_api_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {internal_api_token}"},
            timeout=httpx.Timeout(12.0),
            transport=transport,
        )

    async def _post_json(
        self,
        path: str,
        *,
        payload: dict[str, Any],
        failure_message: str,
        rejected_message: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[httpx.Response, object]:
        try:
            async with asyncio.timeout(12.0):
                response = await self._client.post(path, json=payload, headers=headers)
        except (httpx.HTTPError, TimeoutError) as error:
            raise BackendError(failure_message) from error

        if rejected_message is None:
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as error:
                raise BackendError(failure_message) from error

        try:
            body = response.json()
        except ValueError as error:
            raise BackendError(failure_message) from error
        if response.is_error and rejected_message is not None:
            detail = body.get("detail", {})
            code = detail.get("code") if isinstance(detail, dict) else None
            raise BackendError(rejected_message, code=code)
        return response, body

    async def resolve_telegram_connection(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        display_name: str | None,
    ) -> TelegramConnectionResult:
        payload: dict[str, Any] = {
            "telegram_user_id": telegram_user_id,
            "username": username,
            "display_name": display_name,
        }
        _, body = await self._post_json(
            "/internal/telegram-connections/resolve",
            payload=payload,
            failure_message="Backend Telegram resolution failed",
            rejected_message="Backend rejected Telegram resolution",
        )
        try:
            parsed = _TelegramInternalResponse.model_validate(body)
        except ValidationError as error:
            raise BackendError("Backend Telegram resolution failed") from error
        return TelegramConnectionResult(kind=parsed.kind)

    async def claim_telegram_link(
        self, *, token: str, telegram_user_id: int, update_id: int,
        username: str | None, display_name: str | None,
    ) -> TelegramConnectionResult:
        payload = {
            "token": token, "telegram_user_id": telegram_user_id,
            "username": username, "display_name": display_name,
        }
        _, body = await self._post_json(
            "/internal/telegram-link-requests/claim",
            payload=payload,
            headers={"Idempotency-Key": f"telegram:update:{update_id}"},
            failure_message="Backend Telegram link failed",
            rejected_message="Backend rejected Telegram link",
        )
        try:
            parsed = _TelegramInternalResponse.model_validate(body)
        except ValidationError as error:
            raise BackendError("Backend Telegram link failed") from error
        return TelegramConnectionResult(kind=parsed.kind)

    async def process_workout_event(
        self,
        *,
        telegram_user_id: int,
        update_id: int,
        action: Literal["open", "log", "complete", "cancel", "undo"],
        text: str | None,
    ) -> WorkoutEventResult:
        payload: dict[str, Any] = {
            "provider": "telegram",
            "provider_subject": str(telegram_user_id),
            "action": action,
            "text": text,
        }
        _, body = await self._post_json(
            "/internal/workout-events",
            payload=payload,
            headers={"Idempotency-Key": f"telegram:update:{update_id}"},
            failure_message="Backend workout event failed",
            rejected_message="Backend rejected workout event",
        )
        try:
            parsed = _WorkoutEventResponse.model_validate(body)
        except ValidationError as error:
            raise BackendError("Backend workout event failed") from error

        added = tuple(_exercise_summary(item) for item in parsed.added_exercises)
        removed = tuple(
            _exercise_summary(item) for item in parsed.removed_exercises
        )
        workout_exercises = parsed.workout.exercises if parsed.workout is not None else ()
        return WorkoutEventResult(
            kind=parsed.kind,
            replayed=parsed.replayed,
            performed_on=parsed.workout.performed_on if parsed.workout is not None else None,
            exercises=added,
            removed_exercises=removed,
            total_exercises=len(workout_exercises),
            total_sets=sum(len(item.sets) for item in workout_exercises),
            clarification_message=parsed.clarification_message,
            program_workout=(
                _program_summary(parsed.workout.program_workout)
                if parsed.workout is not None and parsed.workout.program_workout is not None
                else None
            ),
            program_history=tuple(
                PreviousExerciseSummary(
                    planned=_planned_summary(item.item),
                    performed_on=item.latest_performed_on,
                    workout_notes=item.latest_workout_notes,
                    occurrences=tuple(_exercise_summary(value) for value in item.latest_occurrences),
                ) for item in parsed.program_history
            ),
        )

    async def process_program_event(
        self, *, telegram_user_id: int, update_id: int,
        action: Literal["new", "create", "edit"],
        day_number: int | None = None, alias: str | None = None,
        selector: str | None = None, text: str | None = None,
        notes: str | None = None,
    ) -> ProgramEventResult:
        payload: dict[str, Any] = {
            "provider": "telegram", "provider_subject": str(telegram_user_id),
            "action": action, "day_number": day_number, "alias": alias,
            "selector": selector, "text": text, "notes": notes,
        }
        payload = {key: value for key, value in payload.items() if value is not None}
        _, body = await self._post_json(
            "/internal/program-events", payload=payload,
            headers={"Idempotency-Key": f"telegram:update:{update_id}"},
            failure_message="Backend program event failed",
            rejected_message="Backend rejected program event",
        )
        try:
            parsed = _ProgramEventResponse.model_validate(body)
        except ValidationError as error:
            raise BackendError("Backend program event failed") from error
        return ProgramEventResult(
            kind=parsed.kind, replayed=parsed.replayed,
            deactivated_count=parsed.deactivated_count,
            program_workout=(
                _program_summary(parsed.program_workout)
                if parsed.program_workout is not None else None
            ),
            clarification_message=parsed.clarification_message,
        )

    async def get_workout_status(
        self,
        *,
        telegram_user_id: int,
    ) -> WorkoutStatusResult:
        payload = {
            "provider": "telegram",
            "provider_subject": str(telegram_user_id),
        }
        _, body = await self._post_json(
            "/internal/workout-status",
            payload=payload,
            failure_message="Backend workout status failed",
            rejected_message="Backend rejected workout status",
        )
        try:
            parsed = _workout_status_adapter.validate_python(body)
        except ValidationError as error:
            raise BackendError("Backend workout status failed") from error

        if isinstance(parsed, _NoActiveWorkoutStatusResponse):
            return WorkoutStatusResult(kind="none")
        return WorkoutStatusResult(
            kind="active",
            workout=WorkoutHistoryItem(
                performed_on=parsed.workout.performed_on,
                notes=parsed.workout.notes,
                exercises=tuple(
                    _exercise_summary(item) for item in parsed.workout.exercises
                ),
                program_workout=(
                    _program_summary(parsed.workout.program_workout)
                    if parsed.workout.program_workout is not None else None
                ),
            ),
        )

    async def query_history(
        self,
        *,
        telegram_user_id: int,
        kind: Literal["workouts", "exercise"],
        query: str | None,
        limit: int,
    ) -> HistoryQueryResult:
        payload: dict[str, Any] = {
            "provider": "telegram",
            "provider_subject": str(telegram_user_id),
            "kind": kind,
            "query": query,
            "limit": limit,
        }
        if query is None:
            payload.pop("query")
        _, body = await self._post_json(
            "/internal/history-queries",
            payload=payload,
            failure_message="Backend history query failed",
            rejected_message="Backend rejected history query",
        )
        try:
            parsed = _history_query_adapter.validate_python(body)
        except ValidationError as error:
            raise BackendError("Backend history query failed") from error

        if isinstance(parsed, _WorkoutHistoryQueryResponse):
            return HistoryQueryResult(
                kind="workouts",
                workouts=tuple(
                    WorkoutHistoryItem(
                        performed_on=item.performed_on,
                        notes=item.notes,
                        exercises=tuple(
                            _exercise_summary(exercise) for exercise in item.exercises
                        ),
                        program_workout=(
                            _program_summary(item.program_workout)
                            if item.program_workout is not None
                            else None
                        ),
                    )
                    for item in parsed.items
                ),
            )
        if isinstance(parsed, _ExerciseHistoryQueryResponse):
            return HistoryQueryResult(
                kind="exercise",
                exercise_name=parsed.exercise.name,
                exercise_workouts=tuple(
                    ExerciseHistoryWorkout(
                        performed_on=item.performed_on,
                        workout_notes=item.workout_notes,
                        occurrences=tuple(
                            _exercise_summary(occurrence)
                            for occurrence in item.occurrences
                        ),
                    )
                    for item in parsed.items
                ),
            )
        if isinstance(parsed, _HistoryClarificationQueryResponse):
            return HistoryQueryResult(
                kind="needs_clarification",
                clarification_message=parsed.clarification_message,
            )
        return HistoryQueryResult(kind="exercise_not_found")

    async def close(self) -> None:
        await self._client.aclose()


def _exercise_summary(value: _WorkoutExerciseResponse) -> ExerciseSummary:
    return ExerciseSummary(
        name=value.exercise.name,
        notes=value.notes,
        sets=tuple(
            SetSummary(
                repetitions=item.repetitions,
                load_value=item.load.value if item.load is not None else None,
                load_unit=item.load.unit if item.load is not None else None,
                notes=item.notes,
            )
            for item in value.sets
        ),
    )


def _planned_summary(value: _ProgramWorkoutItemResponse) -> PlannedExerciseSummary:
    return PlannedExerciseSummary(
        name=value.exercise_name, target_sets=value.target_sets,
        target_repetitions=value.target_repetitions,
        rest_seconds=value.rest_seconds,
    )


def _program_summary(value: _ProgramWorkoutResponse) -> ProgramWorkoutSummary:
    return ProgramWorkoutSummary(
        day_number=value.day_number, alias=value.alias, notes=value.notes,
        items=tuple(_planned_summary(item) for item in value.items),
    )
