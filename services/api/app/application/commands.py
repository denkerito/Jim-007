"""Validated, transport-independent commands accepted by application services."""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.models import ExternalIdentity, LoadUnit, User, Workout, WorkoutExercise


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


class CompleteWorkoutCommand(CommandModel):
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
