from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.application.exercises import CreateExercise, RenameExercise
from app.domain.exceptions import (
    ExerciseNameConflictError,
    InvalidExerciseNameError,
    NotFoundError,
)
from app.domain.models import Exercise


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.users = AsyncMock()
        self.exercises = AsyncMock()
        self.commit = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


def exercise(*, user_id, name: str = "Bench Press") -> Exercise:
    return Exercise(
        id=uuid4(),
        user_id=user_id,
        name=name,
        normalized_name=name.casefold(),
        created_at=datetime(2026, 8, 24, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_create_exercise_cleans_and_normalizes_the_name() -> None:
    user_id = uuid4()
    uow = FakeUnitOfWork()
    created = exercise(user_id=user_id)
    uow.users.get_by_id.return_value = object()
    uow.exercises.get_or_create.return_value = (created, True)

    result = await CreateExercise(lambda: uow).execute(
        user_id=user_id,
        name="  Bench\t Press  ",
    )

    assert result.exercise == created
    assert result.created is True
    uow.exercises.get_or_create.assert_awaited_once()
    call = uow.exercises.get_or_create.await_args.kwargs
    assert call["user_id"] == user_id
    assert call["name"] == "Bench Press"
    assert call["normalized_name"] == "bench press"
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_exercise_returns_the_existing_spelling() -> None:
    user_id = uuid4()
    uow = FakeUnitOfWork()
    existing = exercise(user_id=user_id, name="Bench Press")
    uow.users.get_by_id.return_value = object()
    uow.exercises.get_or_create.return_value = (existing, False)

    result = await CreateExercise(lambda: uow).execute(
        user_id=user_id,
        name="bench press",
    )

    assert result.exercise.name == "Bench Press"
    assert result.created is False
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [" \t ", "x" * 256])
async def test_create_exercise_rejects_invalid_names(name: str) -> None:
    uow = FakeUnitOfWork()
    uow.users.get_by_id.return_value = object()

    with pytest.raises(InvalidExerciseNameError):
        await CreateExercise(lambda: uow).execute(user_id=uuid4(), name=name)

    uow.exercises.get_or_create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_exercise_requires_an_existing_user() -> None:
    uow = FakeUnitOfWork()
    uow.users.get_by_id.return_value = None

    with pytest.raises(NotFoundError):
        await CreateExercise(lambda: uow).execute(user_id=uuid4(), name="Squat")

    uow.exercises.get_or_create.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_exercise_cleans_and_normalizes_the_name() -> None:
    user_id = uuid4()
    exercise_id = uuid4()
    uow = FakeUnitOfWork()
    renamed = exercise(user_id=user_id, name="Bench Press")
    uow.exercises.rename.return_value = renamed

    result = await RenameExercise(lambda: uow).execute(
        user_id=user_id,
        exercise_id=exercise_id,
        name="  Bench\t Press  ",
    )

    assert result == renamed
    uow.exercises.rename.assert_awaited_once_with(
        exercise_id=exercise_id,
        user_id=user_id,
        name="Bench Press",
        normalized_name="bench press",
    )
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_rename_exercise_allows_display_name_only_changes() -> None:
    user_id = uuid4()
    exercise_id = uuid4()
    uow = FakeUnitOfWork()
    renamed = exercise(user_id=user_id, name="BENCH PRESS")
    uow.exercises.rename.return_value = renamed

    result = await RenameExercise(lambda: uow).execute(
        user_id=user_id,
        exercise_id=exercise_id,
        name="BENCH PRESS",
    )

    assert result.name == "BENCH PRESS"
    assert uow.exercises.rename.await_args.kwargs["normalized_name"] == "bench press"
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [" \t ", "x" * 256])
async def test_rename_exercise_rejects_invalid_names(name: str) -> None:
    uow = FakeUnitOfWork()

    with pytest.raises(InvalidExerciseNameError):
        await RenameExercise(lambda: uow).execute(
            user_id=uuid4(), exercise_id=uuid4(), name=name
        )

    uow.exercises.rename.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_exercise_requires_an_owned_exercise() -> None:
    uow = FakeUnitOfWork()
    uow.exercises.rename.return_value = None

    with pytest.raises(NotFoundError):
        await RenameExercise(lambda: uow).execute(
            user_id=uuid4(), exercise_id=uuid4(), name="Squat"
        )

    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rename_exercise_propagates_name_conflicts() -> None:
    uow = FakeUnitOfWork()
    uow.exercises.rename.side_effect = ExerciseNameConflictError(
        "An exercise with this name already exists"
    )

    with pytest.raises(ExerciseNameConflictError):
        await RenameExercise(lambda: uow).execute(
            user_id=uuid4(), exercise_id=uuid4(), name="Squat"
        )

    uow.commit.assert_not_awaited()
