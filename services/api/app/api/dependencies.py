"""Shared FastAPI dependencies for application services."""

from functools import partial
from typing import Annotated

from fastapi import Depends

from app.application.ports import UnitOfWorkFactory
from app.infrastructure.database.session import session_factory
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork


def get_uow_factory() -> UnitOfWorkFactory:
    return partial(SqlAlchemyUnitOfWork, session_factory)


UowFactory = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]
