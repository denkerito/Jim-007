"""Validated, transport-independent commands accepted by application services."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models import (
    Exercise,
    ExternalIdentity,
    LoadUnit,
    User,
    Workout,
    WorkoutExercise,
)


class CommandModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ExistingExerciseReference(CommandModel):
    kind: Literal["existing"]
    exercise_id: UUID


class NewExerciseReference(CommandModel):
    kind: Literal["new"]
    name: str = Field(min_length=1, max_length=255)

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("exercise name must not be blank")
        return value


ExerciseReference = Annotated[
    ExistingExerciseReference | NewExerciseReference,
    Field(discriminator="kind"),
]


class PerformedSetInput(CommandModel):
    repetitions: int = Field(gt=0, le=32767)
    load_value: Decimal | None = Field(default=None, ge=0, max_digits=10, decimal_places=3)
    load_unit: LoadUnit | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def unit_requires_value(self) -> "PerformedSetInput":
        if self.load_value is None and self.load_unit is not None:
            raise ValueError("load_unit requires load_value")
        return self


class InterpretationStatus(StrEnum):
    READY = "ready"
    NEEDS_CLARIFICATION = "needs_clarification"


class ExerciseResolutionStatus(StrEnum):
    MATCHED = "matched"
    NOT_FOUND = "not_found"
    NEEDS_CLARIFICATION = "needs_clarification"


class WorkoutEventAction(StrEnum):
    OPEN = "open"
    LOG = "log"
    COMPLETE = "complete"
    CANCEL = "cancel"
    UNDO = "undo"


class HistoryQueryKind(StrEnum):
    WORKOUTS = "workouts"
    EXERCISE = "exercise"


class ExerciseCatalogItem(CommandModel):
    id: UUID
    name: str


class ExerciseQueryInterpretation(CommandModel):
    status: ExerciseResolutionStatus
    exercise_id: UUID | None = None
    clarification_message: str | None = None

    @model_validator(mode="after")
    def status_must_match_payload(self) -> "ExerciseQueryInterpretation":
        if self.status is ExerciseResolutionStatus.MATCHED:
            if self.exercise_id is None or self.clarification_message is not None:
                raise ValueError("a matched exercise query requires only exercise_id")
        elif self.status is ExerciseResolutionStatus.NOT_FOUND:
            if self.exercise_id is not None or self.clarification_message is not None:
                raise ValueError("a not-found exercise query must not include a payload")
        elif self.exercise_id is not None or not (
            self.clarification_message or ""
        ).strip():
            raise ValueError("a clarification requires a message and no exercise_id")
        return self


class WorkoutInterpretationContext(CommandModel):
    locale: str
    timezone: str
    current_date: date
    preferred_load_unit: LoadUnit


class InterpretedExercise(CommandModel):
    catalog_exercise_id: UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    sets: tuple[PerformedSetInput, ...] = Field(min_length=1)
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("exercise name must not be blank")
        return value


class WorkoutDateInterpretation(CommandModel):
    status: InterpretationStatus
    performed_on: date | None = None
    notes: str | None = None
    clarification_message: str | None = None

    @model_validator(mode="after")
    def status_must_match_payload(self) -> "WorkoutDateInterpretation":
        if self.status is InterpretationStatus.READY:
            if self.performed_on is None or self.clarification_message is not None:
                raise ValueError("a ready date interpretation requires only performed_on")
        elif self.performed_on is not None or not (self.clarification_message or "").strip():
            raise ValueError("a clarification requires a message and no performed_on")
        return self


class WorkoutLogInterpretation(CommandModel):
    status: InterpretationStatus
    exercises: tuple[InterpretedExercise, ...] = ()
    clarification_message: str | None = None

    @model_validator(mode="after")
    def status_must_match_payload(self) -> "WorkoutLogInterpretation":
        if self.status is InterpretationStatus.READY:
            if not self.exercises or self.clarification_message is not None:
                raise ValueError("a ready log interpretation requires exercises only")
        elif self.exercises or not (self.clarification_message or "").strip():
            raise ValueError("a clarification requires a message and no exercises")
        return self


class CreateWorkoutCommand(CommandModel):
    user_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    performed_on: date | None = None
    notes: str | None = None


class AddExerciseToWorkoutCommand(CommandModel):
    user_id: UUID
    workout_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    exercise: ExerciseReference
    sets: tuple[PerformedSetInput, ...] = Field(min_length=1)
    notes: str | None = None


class LogWorkoutMessageCommand(CommandModel):
    user_id: UUID
    workout_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    exercises: tuple[InterpretedExercise, ...] = Field(min_length=1)


class CompleteWorkoutCommand(CommandModel):
    user_id: UUID
    workout_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class CancelWorkoutCommand(CommandModel):
    user_id: UUID
    workout_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class UndoWorkoutMessageCommand(CommandModel):
    user_id: UUID
    workout_id: UUID
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class RegisterExternalIdentityCommand(CommandModel):
    provider: str = Field(min_length=1, max_length=32)
    provider_subject: str = Field(min_length=1, max_length=255)
    username: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)

    @field_validator("provider", "provider_subject")
    @classmethod
    def identity_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ProcessWorkoutEventCommand(CommandModel):
    provider: str = Field(min_length=1, max_length=32)
    provider_subject: str = Field(min_length=1, max_length=255)
    action: WorkoutEventAction
    text: str | None = Field(default=None, max_length=4096)
    idempotency_key: str = Field(min_length=1, max_length=255)
    request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def action_must_match_text(self) -> "ProcessWorkoutEventCommand":
        text = (self.text or "").strip()
        if self.action is WorkoutEventAction.LOG and not text:
            raise ValueError("log events require text")
        if self.action in {
            WorkoutEventAction.COMPLETE,
            WorkoutEventAction.CANCEL,
            WorkoutEventAction.UNDO,
        } and self.text is not None:
            raise ValueError(f"{self.action.value} events do not accept text")
        return self


class GetWorkoutStatusCommand(CommandModel):
    provider: str = Field(min_length=1, max_length=32)
    provider_subject: str = Field(min_length=1, max_length=255)

    @field_validator("provider", "provider_subject")
    @classmethod
    def identity_text_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class ProcessHistoryQueryCommand(CommandModel):
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
    def kind_must_match_query(self) -> "ProcessHistoryQueryCommand":
        query = (self.query or "").strip()
        if self.kind is HistoryQueryKind.EXERCISE and not query:
            raise ValueError("exercise history queries require query")
        if self.kind is HistoryQueryKind.WORKOUTS and self.query is not None:
            raise ValueError("workout history queries do not accept query")
        return self


class HistoryCursor(CommandModel):
    version: Literal[1] = 1
    performed_on: date
    created_at: datetime
    workout_id: UUID

    @field_validator("created_at")
    @classmethod
    def created_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("created_at must include a timezone")
        return value


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user: User
    identity: ExternalIdentity
    created: bool


ResultT = TypeVar("ResultT", Workout, WorkoutExercise)


@dataclass(frozen=True, slots=True)
class CommandResult(Generic[ResultT]):
    value: ResultT
    replayed: bool


@dataclass(frozen=True, slots=True)
class LogWorkoutMessageResult:
    workout: Workout
    added_exercises: tuple[WorkoutExercise, ...]
    replayed: bool


@dataclass(frozen=True, slots=True)
class CancelWorkoutResult:
    workout_id: UUID
    replayed: bool


@dataclass(frozen=True, slots=True)
class UndoWorkoutMessageResult:
    workout: Workout
    removed_exercises: tuple[WorkoutExercise, ...]
    replayed: bool


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
    workout: Workout | None = None
    added_exercises: tuple[WorkoutExercise, ...] = ()
    removed_exercises: tuple[WorkoutExercise, ...] = ()
    clarification_message: str | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class WorkoutStatusResult:
    kind: Literal["none", "active"]
    workout: Workout | None = None


@dataclass(frozen=True, slots=True)
class WorkoutHistoryPage:
    items: tuple[Workout, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class ExerciseHistoryItem:
    workout_id: UUID
    performed_on: date
    workout_notes: str | None
    workout_created_at: datetime
    occurrences: tuple[WorkoutExercise, ...]


@dataclass(frozen=True, slots=True)
class ExerciseHistoryPage:
    exercise: Exercise
    items: tuple[ExerciseHistoryItem, ...]
    next_cursor: str | None = None


@dataclass(frozen=True, slots=True)
class HistoryQueryResult:
    kind: Literal[
        "workouts",
        "exercise",
        "exercise_not_found",
        "needs_clarification",
    ]
    workout_history: WorkoutHistoryPage | None = None
    exercise_history: ExerciseHistoryPage | None = None
    clarification_message: str | None = None
