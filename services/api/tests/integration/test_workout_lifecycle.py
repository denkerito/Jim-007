import asyncio
from datetime import date
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.commands import (
    AddExerciseToWorkoutCommand,
    CompleteWorkoutCommand,
    CreateWorkoutCommand,
    NewExerciseReference,
    PerformedSetInput,
)
from app.application.services import AddExerciseToWorkout, CompleteWorkout, CreateWorkout
from app.domain.exceptions import (
    ActiveWorkoutExistsError,
    IdempotencyConflictError,
    WorkoutNotEditableError,
)
from app.domain.models import WorkoutStatus
from app.infrastructure.database.models import Exercise, PerformedSet, WorkoutExercise
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def factory(session_factory: async_sessionmaker[AsyncSession]):
    return lambda: SqlAlchemyUnitOfWork(session_factory)


@pytest.mark.asyncio
async def test_incremental_lifecycle_and_replay(session_factory, user_id) -> None:
    uow = factory(session_factory)
    create_command = CreateWorkoutCommand(
        user_id=user_id,
        idempotency_key="create:1",
        request_hash=HASH_A,
        performed_on=date(2026, 8, 3),
    )
    created = await CreateWorkout(uow).execute(create_command)
    replayed = await CreateWorkout(uow).execute(create_command)
    assert created.value.status is WorkoutStatus.DRAFT
    assert replayed.replayed is True
    assert replayed.value.id == created.value.id

    add_command = AddExerciseToWorkoutCommand(
        user_id=user_id,
        workout_id=created.value.id,
        idempotency_key="add:1",
        request_hash=HASH_B,
        exercise=NewExerciseReference(kind="new", name="  Bench   Press "),
        sets=(
            PerformedSetInput(repetitions=8, load_value="80"),
            PerformedSetInput(repetitions=7, load_value="80"),
        ),
    )
    added = await AddExerciseToWorkout(uow).execute(add_command)
    add_replay = await AddExerciseToWorkout(uow).execute(add_command)
    assert add_replay.replayed is True
    assert add_replay.value.id == added.value.id
    assert tuple(item.set_number for item in added.value.sets) == (1, 2)
    assert all(item.load is not None and item.load.unit.value == "kg" for item in added.value.sets)

    completed = await CompleteWorkout(uow).execute(
        CompleteWorkoutCommand(
            user_id=user_id,
            workout_id=created.value.id,
            idempotency_key="complete:1",
            request_hash=HASH_C,
        )
    )
    assert completed.value.status is WorkoutStatus.COMPLETED
    assert len(completed.value.exercises) == 1


@pytest.mark.asyncio
async def test_only_one_draft_per_user(session_factory, user_id) -> None:
    use_case = CreateWorkout(factory(session_factory))
    await use_case.execute(
        CreateWorkoutCommand(
            user_id=user_id, idempotency_key="create:1", request_hash=HASH_A
        )
    )
    with pytest.raises(ActiveWorkoutExistsError):
        await use_case.execute(
            CreateWorkoutCommand(
                user_id=user_id, idempotency_key="create:2", request_hash=HASH_B
            )
        )


@pytest.mark.asyncio
async def test_changed_payload_conflicts_with_existing_key(session_factory, user_id) -> None:
    use_case = CreateWorkout(factory(session_factory))
    await use_case.execute(
        CreateWorkoutCommand(
            user_id=user_id, idempotency_key="same-key", request_hash=HASH_A
        )
    )
    with pytest.raises(IdempotencyConflictError):
        await use_case.execute(
            CreateWorkoutCommand(
                user_id=user_id, idempotency_key="same-key", request_hash=HASH_B
            )
        )


@pytest.mark.asyncio
async def test_concurrent_replay_creates_one_workout(session_factory, user_id) -> None:
    use_case = CreateWorkout(factory(session_factory))
    command = CreateWorkoutCommand(
        user_id=user_id, idempotency_key="concurrent", request_hash=HASH_A
    )
    first, second = await asyncio.gather(
        use_case.execute(command), use_case.execute(command)
    )
    assert first.value.id == second.value.id
    assert sorted((first.replayed, second.replayed)) == [False, True]


@pytest.mark.asyncio
async def test_uow_rolls_back_all_uncommitted_rows(session_factory, user_id) -> None:
    uow_factory = factory(session_factory)
    workout_id = uuid4()
    async with uow_factory() as uow:
        await uow.workouts.create(
            workout_id=workout_id,
            user_id=user_id,
            performed_on=date(2026, 8, 3),
            notes=None,
        )

    async with uow_factory() as uow:
        assert await uow.workouts.get_by_id(workout_id, user_id) is None


@pytest.mark.asyncio
async def test_concurrent_equivalent_names_reuse_catalog_exercise(
    session_factory, user_id
) -> None:
    uow = factory(session_factory)
    workout = await CreateWorkout(uow).execute(
        CreateWorkoutCommand(
            user_id=user_id, idempotency_key="create", request_hash=HASH_A
        )
    )
    commands = (
        AddExerciseToWorkoutCommand(
            user_id=user_id,
            workout_id=workout.value.id,
            idempotency_key="add:a",
            request_hash=HASH_B,
            exercise=NewExerciseReference(kind="new", name="Bench   Press"),
            sets=(PerformedSetInput(repetitions=8),),
        ),
        AddExerciseToWorkoutCommand(
            user_id=user_id,
            workout_id=workout.value.id,
            idempotency_key="add:b",
            request_hash=HASH_C,
            exercise=NewExerciseReference(kind="new", name="ＢＥＮＣＨ press"),
            sets=(PerformedSetInput(repetitions=10),),
        ),
    )
    results = await asyncio.gather(
        *(AddExerciseToWorkout(uow).execute(command) for command in commands)
    )
    assert {result.value.position for result in results} == {1, 2}
    assert len({result.value.exercise.id for result in results}) == 1

    async with session_factory() as session:
        assert await session.scalar(select(func.count()).select_from(Exercise)) == 1
        assert await session.scalar(select(func.count()).select_from(WorkoutExercise)) == 2
        assert await session.scalar(select(func.count()).select_from(PerformedSet)) == 2


@pytest.mark.asyncio
async def test_add_and_complete_are_serialized_without_partial_blocks(
    session_factory, user_id
) -> None:
    uow = factory(session_factory)
    workout = await CreateWorkout(uow).execute(
        CreateWorkoutCommand(
            user_id=user_id, idempotency_key="create", request_hash=HASH_A
        )
    )
    await AddExerciseToWorkout(uow).execute(
        AddExerciseToWorkoutCommand(
            user_id=user_id,
            workout_id=workout.value.id,
            idempotency_key="initial",
            request_hash=HASH_B,
            exercise=NewExerciseReference(kind="new", name="Bench Press"),
            sets=(PerformedSetInput(repetitions=8),),
        )
    )
    add_command = AddExerciseToWorkoutCommand(
        user_id=user_id,
        workout_id=workout.value.id,
        idempotency_key="racing-add",
        request_hash=HASH_C,
        exercise=NewExerciseReference(kind="new", name="Lat Machine"),
        sets=(PerformedSetInput(repetitions=10), PerformedSetInput(repetitions=10)),
    )
    complete_command = CompleteWorkoutCommand(
        user_id=user_id,
        workout_id=workout.value.id,
        idempotency_key="racing-complete",
        request_hash="d" * 64,
    )
    add_result, complete_result = await asyncio.gather(
        AddExerciseToWorkout(uow).execute(add_command),
        CompleteWorkout(uow).execute(complete_command),
        return_exceptions=True,
    )
    if isinstance(add_result, Exception):
        assert isinstance(add_result, WorkoutNotEditableError)
    else:
        assert len(add_result.value.sets) == 2
    assert not isinstance(complete_result, Exception)

    async with uow() as check:
        persisted = await check.workouts.get_by_id(workout.value.id, user_id)
        assert persisted is not None
        assert persisted.status is WorkoutStatus.COMPLETED
        assert len(persisted.exercises) in (1, 2)
        assert all(len(item.sets) in (1, 2) for item in persisted.exercises)
