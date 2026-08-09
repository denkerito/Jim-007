"""Shared FastAPI dependencies for application services."""

from functools import partial
from typing import Annotated, cast

from fastapi import Depends, Request

from app.application.ports import (
    ExerciseQueryInterpreter,
    UnitOfWorkFactory,
    WorkoutTextInterpreter,
)
from app.infrastructure.database.session import session_factory
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.infrastructure.email import SmtpEmailSender
from app.infrastructure.security import PasswordService
from app.config import Settings, get_settings


def get_uow_factory() -> UnitOfWorkFactory:
    return partial(SqlAlchemyUnitOfWork, session_factory)


UowFactory = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


def get_workout_text_interpreter(request: Request) -> WorkoutTextInterpreter:
    return cast(WorkoutTextInterpreter, request.app.state.workout_text_interpreter)


WorkoutInterpreter = Annotated[
    WorkoutTextInterpreter,
    Depends(get_workout_text_interpreter),
]


def get_exercise_query_interpreter(request: Request) -> ExerciseQueryInterpreter:
    return cast(ExerciseQueryInterpreter, request.app.state.exercise_query_interpreter)


ExerciseHistoryInterpreter = Annotated[
    ExerciseQueryInterpreter,
    Depends(get_exercise_query_interpreter),
]


def get_password_service() -> PasswordService:
    return PasswordService()


PasswordHasher = Annotated[PasswordService, Depends(get_password_service)]


def get_email_sender(settings: Annotated[Settings, Depends(get_settings)]) -> SmtpEmailSender:
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        sender=settings.email_from,
        username=settings.smtp_username,
        password=(
            settings.smtp_password.get_secret_value()
            if settings.smtp_password is not None else None
        ),
        starttls=settings.smtp_starttls,
    )


EmailSender = Annotated[SmtpEmailSender, Depends(get_email_sender)]
