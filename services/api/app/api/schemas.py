"""HTTP request and response schemas, deliberately separate from domain models."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain import models as domain


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


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
