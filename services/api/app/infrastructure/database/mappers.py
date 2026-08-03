"""Explicit conversion from persistence models to immutable domain models."""

from app.domain.models import (
    Exercise,
    ExternalIdentity,
    Load,
    LoadUnit,
    PerformedSet,
    User,
    Workout,
    WorkoutExercise,
    WorkoutStatus,
)
from app.infrastructure.database import models as orm


def to_user(model: orm.AppUser) -> User:
    return User(
        id=model.id,
        locale=model.locale,
        timezone=model.timezone,
        preferred_load_unit=LoadUnit(model.preferred_load_unit),
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


def to_exercise(model: orm.Exercise) -> Exercise:
    return Exercise(
        id=model.id,
        user_id=model.user_id,
        name=model.name,
        normalized_name=model.normalized_name,
        created_at=model.created_at,
    )


def to_external_identity(model: orm.ExternalIdentity) -> ExternalIdentity:
    return ExternalIdentity(
        id=model.id,
        user_id=model.user_id,
        provider=model.provider,
        provider_subject=model.provider_subject,
        username=model.username,
        display_name=model.display_name,
        created_at=model.created_at,
    )


def to_workout_exercise(model: orm.WorkoutExercise) -> WorkoutExercise:
    return WorkoutExercise(
        id=model.id,
        exercise=to_exercise(model.exercise),
        position=model.position,
        notes=model.notes,
        sets=tuple(
            PerformedSet(
                id=item.id,
                set_number=item.set_number,
                repetitions=item.repetitions,
                load=(
                    Load(value=item.load_value, unit=LoadUnit(item.load_unit))
                    if item.load_value is not None and item.load_unit is not None
                    else None
                ),
                notes=item.notes,
            )
            for item in model.sets
        ),
    )


def to_workout(model: orm.Workout) -> Workout:
    return Workout(
        id=model.id,
        user_id=model.user_id,
        performed_on=model.performed_on,
        status=WorkoutStatus(model.status),
        notes=model.notes,
        created_at=model.created_at,
        completed_at=model.completed_at,
        exercises=tuple(to_workout_exercise(item) for item in model.exercises),
    )
