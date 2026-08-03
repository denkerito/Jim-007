"""SQLAlchemy transaction boundary for application use cases."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.infrastructure.database.repositories import (
    SqlAlchemyExerciseRepository,
    SqlAlchemyProcessedCommandRepository,
    SqlAlchemyUserRepository,
    SqlAlchemyWorkoutRepository,
)


class SqlAlchemyUnitOfWork:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None
        self._committed = False

    async def __aenter__(self) -> "SqlAlchemyUnitOfWork":
        self._session = self._session_factory()
        self.users = SqlAlchemyUserRepository(self._session)
        self.exercises = SqlAlchemyExerciseRepository(self._session)
        self.workouts = SqlAlchemyWorkoutRepository(self._session)
        self.processed_commands = SqlAlchemyProcessedCommandRepository(self._session)
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self._session is None:
            return
        try:
            if exc_type is not None or not self._committed:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    async def commit(self) -> None:
        if self._session is None:
            raise RuntimeError("Unit of Work has not been entered")
        await self._session.commit()
        self._committed = True

    async def rollback(self) -> None:
        if self._session is None:
            raise RuntimeError("Unit of Work has not been entered")
        await self._session.rollback()
        self._committed = False
