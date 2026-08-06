"""HTTP request and response schemas, deliberately separate from domain models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.application.commands import (
    ExerciseHistoryPage,
    HistoryQueryKind,
    HistoryQueryResult,
    WorkoutEventAction,
    WorkoutEventResult,
    WorkoutHistoryPage,
)
from app.domain import models as domain


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TelegramRegistrationRequest(ApiModel):
    telegram_user_id: int = Field(gt=0, le=9_223_372_036_854_775_807)
    username: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class UserRegistrationResponse(ApiModel):
    user_id: UUID
    locale: str
    timezone: str
    preferred_load_unit: domain.LoadUnit


class CreateWorkoutRequest(ApiModel):
    performed_on: date | None = None
    notes: str | None = None


class ExistingExerciseRequest(ApiModel):
    kind: Literal["existing"]
    exercise_id: UUID


class NewExerciseRequest(ApiModel):
    kind: Literal["new"]
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("exercise name must not be blank")
        return value


ExerciseRequest = Annotated[
    ExistingExerciseRequest | NewExerciseRequest,
    Field(discriminator="kind"),
]


class PerformedSetRequest(ApiModel):
    repetitions: int = Field(gt=0, le=32767)
    load_value: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=3)
    load_unit: domain.LoadUnit | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def unit_requires_value(self) -> "PerformedSetRequest":
        if self.load_value is None and self.load_unit is not None:
            raise ValueError("load_unit requires load_value")
        return self


class AddExerciseRequest(ApiModel):
    exercise: ExerciseRequest
    sets: tuple[PerformedSetRequest, ...] = Field(min_length=1)
    notes: str | None = None


class WorkoutEventRequest(ApiModel):
    provider: str = Field(min_length=1, max_length=32)
    provider_subject: str = Field(min_length=1, max_length=255)
    action: WorkoutEventAction
    text: str | None = Field(default=None, max_length=4096)

    @model_validator(mode="after")
    def action_must_match_text(self) -> "WorkoutEventRequest":
        text = (self.text or "").strip()
        if self.action is WorkoutEventAction.LOG and not text:
            raise ValueError("log events require text")
        if self.action is WorkoutEventAction.COMPLETE and self.text is not None:
            raise ValueError("complete events do not accept text")
        return self


class LoadResponse(ApiModel):
    value: Decimal
    unit: domain.LoadUnit
    kilograms: Decimal


class PerformedSetResponse(ApiModel):
    id: UUID
    set_number: int
    repetitions: int
    load: LoadResponse | None
    notes: str | None


class ExerciseResponse(ApiModel):
    id: UUID
    name: str
    normalized_name: str


class WorkoutExerciseResponse(ApiModel):
    id: UUID
    exercise: ExerciseResponse
    position: int
    notes: str | None
    sets: tuple[PerformedSetResponse, ...]


class WorkoutResponse(ApiModel):
    id: UUID
    user_id: UUID
    performed_on: date
    status: domain.WorkoutStatus
    notes: str | None
    created_at: datetime
    completed_at: datetime | None
    exercises: tuple[WorkoutExerciseResponse, ...]


class WorkoutEventResponse(ApiModel):
    kind: Literal["opened", "logged", "completed", "needs_clarification"]
    replayed: bool = False
    workout: WorkoutResponse | None = None
    added_exercises: tuple[WorkoutExerciseResponse, ...] = ()
    clarification_message: str | None = None


class WorkoutHistoryPageResponse(ApiModel):
    items: tuple[WorkoutResponse, ...]
    next_cursor: str | None = None


class ExerciseHistoryItemResponse(ApiModel):
    workout_id: UUID
    performed_on: date
    workout_notes: str | None
    occurrences: tuple[WorkoutExerciseResponse, ...]


class ExerciseHistoryPageResponse(ApiModel):
    exercise: ExerciseResponse
    items: tuple[ExerciseHistoryItemResponse, ...]
    next_cursor: str | None = None


class HistoryQueryRequest(ApiModel):
    provider: str = Field(min_length=1, max_length=32)
    provider_subject: str = Field(min_length=1, max_length=255)
    kind: HistoryQueryKind
    query: str | None = Field(default=None, max_length=255)
    limit: int = Field(default=5, ge=1, le=20)
    cursor: str | None = Field(default=None, min_length=1, max_length=1024)

    @field_validator("provider", "provider_subject")
    @classmethod
    def identity_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @model_validator(mode="after")
    def kind_must_match_query(self) -> "HistoryQueryRequest":
        query = (self.query or "").strip()
        if self.kind is HistoryQueryKind.EXERCISE and not query:
            raise ValueError("exercise history queries require query")
        if self.kind is HistoryQueryKind.WORKOUTS and self.query is not None:
            raise ValueError("workout history queries do not accept query")
        return self


class WorkoutHistoryQueryResponse(ApiModel):
    kind: Literal["workouts"]
    items: tuple[WorkoutResponse, ...]
    next_cursor: str | None = None


class ExerciseHistoryQueryResponse(ApiModel):
    kind: Literal["exercise"]
    exercise: ExerciseResponse
    items: tuple[ExerciseHistoryItemResponse, ...]
    next_cursor: str | None = None


class ExerciseNotFoundQueryResponse(ApiModel):
    kind: Literal["exercise_not_found"]


class HistoryClarificationQueryResponse(ApiModel):
    kind: Literal["needs_clarification"]
    clarification_message: str


HistoryQueryResponse = Annotated[
    WorkoutHistoryQueryResponse
    | ExerciseHistoryQueryResponse
    | ExerciseNotFoundQueryResponse
    | HistoryClarificationQueryResponse,
    Field(discriminator="kind"),
]


def workout_exercise_response(value: domain.WorkoutExercise) -> WorkoutExerciseResponse:
    return WorkoutExerciseResponse(
        id=value.id,
        exercise=ExerciseResponse(
            id=value.exercise.id,
            name=value.exercise.name,
            normalized_name=value.exercise.normalized_name,
        ),
        position=value.position,
        notes=value.notes,
        sets=tuple(
            PerformedSetResponse(
                id=item.id,
                set_number=item.set_number,
                repetitions=item.repetitions,
                load=(
                    LoadResponse(
                        value=item.load.value,
                        unit=item.load.unit,
                        kilograms=item.load.kilograms,
                    )
                    if item.load is not None
                    else None
                ),
                notes=item.notes,
            )
            for item in value.sets
        ),
    )


def workout_response(value: domain.Workout) -> WorkoutResponse:
    return WorkoutResponse(
        id=value.id,
        user_id=value.user_id,
        performed_on=value.performed_on,
        status=value.status,
        notes=value.notes,
        created_at=value.created_at,
        completed_at=value.completed_at,
        exercises=tuple(workout_exercise_response(item) for item in value.exercises),
    )


def workout_event_response(value: WorkoutEventResult) -> WorkoutEventResponse:
    return WorkoutEventResponse(
        kind=value.kind,
        replayed=value.replayed,
        workout=workout_response(value.workout) if value.workout is not None else None,
        added_exercises=tuple(
            workout_exercise_response(item) for item in value.added_exercises
        ),
        clarification_message=value.clarification_message,
    )


def workout_history_page_response(
    value: WorkoutHistoryPage,
) -> WorkoutHistoryPageResponse:
    return WorkoutHistoryPageResponse(
        items=tuple(workout_response(item) for item in value.items),
        next_cursor=value.next_cursor,
    )


def exercise_history_page_response(
    value: ExerciseHistoryPage,
) -> ExerciseHistoryPageResponse:
    return ExerciseHistoryPageResponse(
        exercise=ExerciseResponse(
            id=value.exercise.id,
            name=value.exercise.name,
            normalized_name=value.exercise.normalized_name,
        ),
        items=tuple(
            ExerciseHistoryItemResponse(
                workout_id=item.workout_id,
                performed_on=item.performed_on,
                workout_notes=item.workout_notes,
                occurrences=tuple(
                    workout_exercise_response(occurrence)
                    for occurrence in item.occurrences
                ),
            )
            for item in value.items
        ),
        next_cursor=value.next_cursor,
    )


def history_query_response(value: HistoryQueryResult) -> HistoryQueryResponse:
    if value.kind == "workouts":
        if value.workout_history is None:
            raise RuntimeError("Workout history result is missing its page")
        page = workout_history_page_response(value.workout_history)
        return WorkoutHistoryQueryResponse(
            kind="workouts",
            items=page.items,
            next_cursor=page.next_cursor,
        )
    if value.kind == "exercise":
        if value.exercise_history is None:
            raise RuntimeError("Exercise history result is missing its page")
        page = exercise_history_page_response(value.exercise_history)
        return ExerciseHistoryQueryResponse(
            kind="exercise",
            exercise=page.exercise,
            items=page.items,
            next_cursor=page.next_cursor,
        )
    if value.kind == "exercise_not_found":
        return ExerciseNotFoundQueryResponse(kind="exercise_not_found")
    if not value.clarification_message:
        raise RuntimeError("Clarification result is missing its message")
    return HistoryClarificationQueryResponse(
        kind="needs_clarification",
        clarification_message=value.clarification_message,
    )
