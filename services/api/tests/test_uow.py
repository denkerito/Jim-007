from unittest.mock import AsyncMock, Mock

import pytest

from app.infrastructure.database.uow import SqlAlchemyUnitOfWork


@pytest.mark.asyncio
async def test_uow_rolls_back_when_not_committed() -> None:
    session = Mock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    factory = Mock(return_value=session)

    async with SqlAlchemyUnitOfWork(factory):  # type: ignore[arg-type]
        pass

    session.rollback.assert_awaited_once()
    session.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_uow_does_not_rollback_after_commit() -> None:
    session = Mock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    factory = Mock(return_value=session)

    async with SqlAlchemyUnitOfWork(factory) as uow:  # type: ignore[arg-type]
        await uow.commit()

    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()
    session.close.assert_awaited_once()
