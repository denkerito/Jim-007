"""Shared FastAPI dependencies for application services."""

from functools import partial
from typing import Annotated, cast

from fastapi import Depends, Request

from app.application.ports import UnitOfWorkFactory, WorkoutTextInterpreter
from app.infrastructure.database.session import session_factory
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork


def get_uow_factory() -> UnitOfWorkFactory:
    return partial(SqlAlchemyUnitOfWork, session_factory)


UowFactory = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


def get_workout_text_interpreter(request: Request) -> WorkoutTextInterpreter:
    return cast(WorkoutTextInterpreter, request.app.state.workout_text_interpreter)


WorkoutInterpreter = Annotated[
    WorkoutTextInterpreter,
    Depends(get_workout_text_interpreter),
]
