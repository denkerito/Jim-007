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
    """Raised when registration cannot be completed or validated."""

    def __init__(self, message: str, *, code: str | None = None) -> None:
        super().__init__(message)
        self.code = code


class _RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: UUID
    locale: str
    timezone: str
    preferred_load_unit: str


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user_id: UUID
    created: bool


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


class _WorkoutResponse(BaseModel):
    performed_on: date
    notes: str | None = None
    exercises: tuple[_WorkoutExerciseResponse, ...]


class _WorkoutEventResponse(BaseModel):
    kind: Literal["opened", "logged", "completed", "needs_clarification"]
    replayed: bool = False
    workout: _WorkoutResponse | None = None
    added_exercises: tuple[_WorkoutExerciseResponse, ...] = ()
    clarification_message: str | None = None


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
class WorkoutEventResult:
    kind: Literal["opened", "logged", "completed", "needs_clarification"]
    replayed: bool
    performed_on: date | None
    exercises: tuple[ExerciseSummary, ...]
    total_exercises: int
    total_sets: int
    clarification_message: str | None


@dataclass(frozen=True, slots=True)
class WorkoutHistoryItem:
    performed_on: date
    notes: str | None
    exercises: tuple[ExerciseSummary, ...]


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

    async def register_telegram_user(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        display_name: str | None,
    ) -> RegistrationResult:
        payload: dict[str, Any] = {
            "telegram_user_id": telegram_user_id,
            "username": username,
            "display_name": display_name,
        }
        try:
            async with asyncio.timeout(12.0):
                response = await self._client.post(
                    "/internal/identities/telegram", json=payload
                )
            response.raise_for_status()
            parsed = _RegistrationResponse.model_validate(response.json())
        except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as error:
            raise BackendError("Backend registration failed") from error

        if response.status_code not in (200, 201):
            raise BackendError("Backend returned an unexpected registration status")
        return RegistrationResult(
            user_id=parsed.user_id,
            created=response.status_code == 201,
        )

    async def process_workout_event(
        self,
        *,
        telegram_user_id: int,
        update_id: int,
        action: Literal["open", "log", "complete"],
        text: str | None,
    ) -> WorkoutEventResult:
        payload: dict[str, Any] = {
            "provider": "telegram",
            "provider_subject": str(telegram_user_id),
            "action": action,
            "text": text,
        }
        try:
            async with asyncio.timeout(12.0):
                response = await self._client.post(
                    "/internal/workout-events",
                    json=payload,
                    headers={"Idempotency-Key": f"telegram:update:{update_id}"},
                )
            if response.is_error:
                detail = response.json().get("detail", {})
                code = detail.get("code") if isinstance(detail, dict) else None
                raise BackendError("Backend rejected workout event", code=code)
            parsed = _WorkoutEventResponse.model_validate(response.json())
        except BackendError:
            raise
        except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as error:
            raise BackendError("Backend workout event failed") from error

        added = tuple(_exercise_summary(item) for item in parsed.added_exercises)
        workout_exercises = parsed.workout.exercises if parsed.workout is not None else ()
        return WorkoutEventResult(
            kind=parsed.kind,
            replayed=parsed.replayed,
            performed_on=parsed.workout.performed_on if parsed.workout is not None else None,
            exercises=added,
            total_exercises=len(workout_exercises),
            total_sets=sum(len(item.sets) for item in workout_exercises),
            clarification_message=parsed.clarification_message,
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
        try:
            async with asyncio.timeout(12.0):
                response = await self._client.post(
                    "/internal/history-queries",
                    json=payload,
                )
            if response.is_error:
                detail = response.json().get("detail", {})
                code = detail.get("code") if isinstance(detail, dict) else None
                raise BackendError("Backend rejected history query", code=code)
            parsed = _history_query_adapter.validate_python(response.json())
        except BackendError:
            raise
        except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as error:
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
