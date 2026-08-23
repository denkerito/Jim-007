"""Explicit conversion from persistence models to immutable domain models."""

from app.domain.models import (
    AuthToken,
    Exercise,
    ExternalIdentity,
    Load,
    LoadUnit,
    PerformedSet,
    ProgramWorkout,
    ProgramWorkoutItem,
    TelegramLinkRequest,
    User,
    WebAccount,
    WebSession,
    Workout,
    WorkoutLogClarification,
    WorkoutLogClarificationStatus,
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


def to_web_account(model: orm.WebAccount) -> WebAccount:
    return WebAccount.model_validate(model, from_attributes=True)


def to_web_session(model: orm.WebSession) -> WebSession:
    return WebSession.model_validate(model, from_attributes=True)


def to_auth_token(model: orm.AuthToken) -> AuthToken:
    return AuthToken.model_validate(model, from_attributes=True)


def to_telegram_link_request(model: orm.TelegramLinkRequest) -> TelegramLinkRequest:
    return TelegramLinkRequest.model_validate(model, from_attributes=True)


def to_workout_log_clarification(
    model: orm.WorkoutLogClarification,
) -> WorkoutLogClarification:
    return WorkoutLogClarification(
        id=model.id,
        user_id=model.user_id,
        workout_id=model.workout_id,
        status=WorkoutLogClarificationStatus(model.status),
        original_text=model.original_text,
        clarification_message=model.clarification_message,
        model=model.model,
        initial_prompt_version=model.initial_prompt_version,
        followup_prompt_version=model.followup_prompt_version,
        created_at=model.created_at,
        expires_at=model.expires_at,
        terminal_at=model.terminal_at,
    )


def to_program_workout(model: orm.ProgramWorkout) -> ProgramWorkout:
    return ProgramWorkout(
        id=model.id,
        user_id=model.user_id,
        day_number=model.day_number,
        alias=model.alias,
        normalized_alias=model.normalized_alias,
        notes=model.notes,
        created_at=model.created_at,
        deactivated_at=model.deactivated_at,
        items=tuple(
            ProgramWorkoutItem(
                id=item.id,
                position=item.position,
                exercise_name=item.exercise_name,
                normalized_exercise_name=item.normalized_exercise_name,
                exercise_id=item.exercise_id,
                target_sets=item.target_sets,
                target_repetitions=item.target_repetitions,
                rest_seconds=item.rest_seconds,
            )
            for item in model.items
        ),
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
        program_workout=(
            to_program_workout(model.program_workout)
            if model.program_workout is not None
            else None
        ),
        exercises=tuple(to_workout_exercise(item) for item in model.exercises),
    )
    TelegramLinkRequest,
    WebAccount,
    WebSession,
