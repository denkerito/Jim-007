"""SQLAlchemy implementations of the application persistence ports."""

from datetime import date
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.ports import ProcessedCommand as CommandRecord
from app.domain.exceptions import ActiveWorkoutExistsError
from app.domain.models import Exercise, Load, User, Workout, WorkoutExercise
from app.infrastructure.database import models as orm
from app.infrastructure.database.mappers import to_exercise, to_user, to_workout, to_workout_exercise


def _workout_options() -> tuple[object, ...]:
    return (
        selectinload(orm.Workout.exercises).selectinload(orm.WorkoutExercise.exercise),
        selectinload(orm.Workout.exercises).selectinload(orm.WorkoutExercise.sets),
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        model = await self._session.get(orm.AppUser, user_id)
        return to_user(model) if model is not None else None


class SqlAlchemyExerciseRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, exercise_id: UUID, user_id: UUID) -> Exercise | None:
        model = await self._session.scalar(
            select(orm.Exercise).where(
                orm.Exercise.id == exercise_id, orm.Exercise.user_id == user_id
            )
        )
        return to_exercise(model) if model is not None else None

    async def get_by_normalized_name(
        self, user_id: UUID, normalized_name: str
    ) -> Exercise | None:
        model = await self._session.scalar(
            select(orm.Exercise).where(
                orm.Exercise.user_id == user_id,
                orm.Exercise.normalized_name == normalized_name,
            )
        )
        return to_exercise(model) if model is not None else None

    async def get_or_create(
        self, *, exercise_id: UUID, user_id: UUID, name: str, normalized_name: str
    ) -> Exercise:
        statement = (
            insert(orm.Exercise)
            .values(
                id=exercise_id,
                user_id=user_id,
                name=name,
                normalized_name=normalized_name,
            )
            .on_conflict_do_nothing(index_elements=["user_id", "normalized_name"])
            .returning(orm.Exercise.id)
        )
        inserted_id = await self._session.scalar(statement)
        await self._session.flush()
        if inserted_id is not None:
            model = await self._session.get(orm.Exercise, inserted_id)
        else:
            model = await self._session.scalar(
                select(orm.Exercise).where(
                    orm.Exercise.user_id == user_id,
                    orm.Exercise.normalized_name == normalized_name,
                )
            )
        if model is None:
            raise RuntimeError("Exercise upsert did not return a row")
        await self._session.refresh(model)
        return to_exercise(model)


class SqlAlchemyWorkoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _load(
        self, workout_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> orm.Workout | None:
        statement = (
            select(orm.Workout)
            .where(orm.Workout.id == workout_id, orm.Workout.user_id == user_id)
            .options(*_workout_options())
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def create(
        self,
        *,
        workout_id: UUID,
        user_id: UUID,
        performed_on: date,
        notes: str | None,
    ) -> Workout:
        statement = (
            insert(orm.Workout)
            .values(
                id=workout_id,
                user_id=user_id,
                performed_on=performed_on,
                notes=notes,
                status="draft",
            )
            .on_conflict_do_nothing(
                index_elements=["user_id"],
                index_where=orm.Workout.status == "draft",
            )
            .returning(orm.Workout.id)
        )
        inserted_id = await self._session.scalar(statement)
        if inserted_id is None:
            active = await self.get_active_draft(user_id)
            raise ActiveWorkoutExistsError(active.id if active is not None else user_id)
        await self._session.flush()
        model = await self._load(inserted_id, user_id)
        if model is None:
            raise RuntimeError("Created workout could not be loaded")
        return to_workout(model)

    async def get_by_id(self, workout_id: UUID, user_id: UUID) -> Workout | None:
        model = await self._load(workout_id, user_id)
        return to_workout(model) if model is not None else None

    async def get_active_draft(self, user_id: UUID) -> Workout | None:
        workout_id = await self._session.scalar(
            select(orm.Workout.id).where(
                orm.Workout.user_id == user_id, orm.Workout.status == "draft"
            )
        )
        return await self.get_by_id(workout_id, user_id) if workout_id is not None else None

    async def get_for_update(self, workout_id: UUID, user_id: UUID) -> Workout | None:
        model = await self._load(workout_id, user_id, for_update=True)
        return to_workout(model) if model is not None else None

    async def append_exercise(
        self,
        *,
        workout_id: UUID,
        user_id: UUID,
        occurrence_id: UUID,
        exercise: Exercise,
        notes: str | None,
        sets: tuple[tuple[UUID, int, Load | None, str | None], ...],
    ) -> WorkoutExercise:
        current_position = await self._session.scalar(
            select(func.coalesce(func.max(orm.WorkoutExercise.position), 0)).where(
                orm.WorkoutExercise.workout_id == workout_id
            )
        )
        model = orm.WorkoutExercise(
            id=occurrence_id,
            user_id=user_id,
            workout_id=workout_id,
            exercise_id=exercise.id,
            position=int(current_position or 0) + 1,
            notes=notes,
        )
        model.sets = [
            orm.PerformedSet(
                id=set_id,
                user_id=user_id,
                set_number=set_number,
                repetitions=repetitions,
                load_value=load.value if load is not None else None,
                load_unit=load.unit.value if load is not None else None,
                load_kg=load.kilograms if load is not None else None,
                notes=set_notes,
            )
            for set_number, (set_id, repetitions, load, set_notes) in enumerate(sets, start=1)
        ]
        self._session.add(model)
        await self._session.flush()
        loaded = await self._session.scalar(
            select(orm.WorkoutExercise)
            .where(orm.WorkoutExercise.id == occurrence_id)
            .options(
                selectinload(orm.WorkoutExercise.exercise),
                selectinload(orm.WorkoutExercise.sets),
            )
        )
        if loaded is None:
            raise RuntimeError("Created workout exercise could not be loaded")
        return to_workout_exercise(loaded)

    async def complete(self, workout_id: UUID, user_id: UUID) -> Workout:
        await self._session.execute(
            update(orm.Workout)
            .where(orm.Workout.id == workout_id, orm.Workout.user_id == user_id)
            .values(status="completed", completed_at=func.now())
        )
        await self._session.flush()
        self._session.expire_all()
        model = await self._load(workout_id, user_id)
        if model is None:
            raise RuntimeError("Completed workout could not be loaded")
        return to_workout(model)


class SqlAlchemyProcessedCommandRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def claim(self, command: CommandRecord) -> bool:
        statement = (
            insert(orm.ProcessedCommand)
            .values(
                idempotency_key=command.idempotency_key,
                user_id=command.user_id,
                operation=command.operation,
                request_hash=command.request_hash,
                resource_id=command.resource_id,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(orm.ProcessedCommand.idempotency_key)
        )
        return await self._session.scalar(statement) is not None

    async def get(self, idempotency_key: str) -> CommandRecord | None:
        model = await self._session.get(orm.ProcessedCommand, idempotency_key)
        if model is None:
            return None
        return CommandRecord(
            idempotency_key=model.idempotency_key,
            user_id=model.user_id,
            operation=model.operation,
            request_hash=model.request_hash,
            resource_id=model.resource_id,
        )
