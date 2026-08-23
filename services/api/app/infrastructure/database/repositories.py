"""SQLAlchemy implementations of the application persistence ports."""

from datetime import date, datetime
from uuid import UUID

from sqlalchemy import delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.application.commands import ExerciseHistoryItem, HistoryCursor
from app.application.ports import ProcessedCommand as CommandRecord
from app.application.statistics import StatisticsSetRecord
from app.domain.exceptions import ActiveWorkoutExistsError
from app.domain.models import (
    Exercise,
    ExternalIdentity,
    Load,
    LoadUnit,
    User,
    Workout,
    WorkoutLogClarification,
    WorkoutExercise,
    ProgramWorkout,
)
from app.infrastructure.database import models as orm
from app.infrastructure.database.mappers import (
    to_exercise,
    to_external_identity,
    to_auth_token,
    to_telegram_link_request,
    to_web_account,
    to_web_session,
    to_user,
    to_workout,
    to_workout_log_clarification,
    to_workout_exercise,
    to_program_workout,
)


def _workout_options() -> tuple[object, ...]:
    return (
        selectinload(orm.Workout.program_workout).selectinload(orm.ProgramWorkout.items),
        selectinload(orm.Workout.exercises).selectinload(orm.WorkoutExercise.exercise),
        selectinload(orm.Workout.exercises).selectinload(orm.WorkoutExercise.sets),
    )


def _program_options() -> tuple[object, ...]:
    return (selectinload(orm.ProgramWorkout.items),)


def _exercise_history_options(exercise_id: UUID) -> tuple[object, ...]:
    matching_occurrences = orm.Workout.exercises.and_(
        orm.WorkoutExercise.exercise_id == exercise_id
    )
    return (
        selectinload(matching_occurrences).selectinload(orm.WorkoutExercise.exercise),
        selectinload(matching_occurrences).selectinload(orm.WorkoutExercise.sets),
    )


def _after_history_cursor(statement, after: HistoryCursor | None):
    if after is None:
        return statement
    return statement.where(
        tuple_(
            orm.Workout.performed_on,
            orm.Workout.created_at,
            orm.Workout.id,
        )
        < tuple_(after.performed_on, after.created_at, after.workout_id)
    )


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, user_id: UUID) -> User | None:
        model = await self._session.get(orm.AppUser, user_id)
        return to_user(model) if model is not None else None

    async def create(
        self,
        *,
        user_id: UUID,
        locale: str,
        timezone: str,
        preferred_load_unit: LoadUnit,
    ) -> User:
        model = orm.AppUser(
            id=user_id,
            locale=locale,
            timezone=timezone,
            preferred_load_unit=preferred_load_unit.value,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return to_user(model)


class SqlAlchemyExternalIdentityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_registration_lock(
        self, provider: str, provider_subject: str
    ) -> None:
        lock_value = f"{provider}\x1f{provider_subject}"
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(lock_value, 0)))
        )

    async def get_by_provider_subject(
        self, provider: str, provider_subject: str
    ) -> ExternalIdentity | None:
        model = await self._session.scalar(
            select(orm.ExternalIdentity).where(
                orm.ExternalIdentity.provider == provider,
                orm.ExternalIdentity.provider_subject == provider_subject,
            )
        )
        return to_external_identity(model) if model is not None else None

    async def get_by_user_provider(
        self, user_id: UUID, provider: str
    ):
        model = await self._session.scalar(
            select(orm.ExternalIdentity).where(
                orm.ExternalIdentity.user_id == user_id,
                orm.ExternalIdentity.provider == provider,
            )
        )
        return to_external_identity(model) if model is not None else None

    async def create(
        self,
        *,
        identity_id: UUID,
        user_id: UUID,
        provider: str,
        provider_subject: str,
        username: str | None,
        display_name: str | None,
    ) -> ExternalIdentity:
        model = orm.ExternalIdentity(
            id=identity_id,
            user_id=user_id,
            provider=provider,
            provider_subject=provider_subject,
            username=username,
            display_name=display_name,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return to_external_identity(model)

    async def update_profile(
        self,
        identity_id: UUID,
        *,
        username: str | None,
        display_name: str | None,
    ) -> ExternalIdentity:
        model = await self._session.get(orm.ExternalIdentity, identity_id)
        if model is None:
            raise RuntimeError("External identity disappeared during registration")
        model.username = username
        model.display_name = display_name
        await self._session.flush()
        await self._session.refresh(model)
        return to_external_identity(model)

    async def delete(self, identity_id: UUID) -> None:
        await self._session.execute(
            delete(orm.ExternalIdentity).where(orm.ExternalIdentity.id == identity_id)
        )


class SqlAlchemyWebAccountRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_email_lock(self, normalized_email: str) -> None:
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(normalized_email, 0)))
        )

    async def get_by_user_id(self, user_id: UUID):
        model = await self._session.get(orm.WebAccount, user_id)
        return to_web_account(model) if model is not None else None

    async def get_by_normalized_email(self, normalized_email: str):
        model = await self._session.scalar(
            select(orm.WebAccount).where(
                orm.WebAccount.normalized_email == normalized_email
            )
        )
        return to_web_account(model) if model is not None else None

    async def create(self, *, user_id: UUID, email: str, normalized_email: str, password_hash: str):
        model = orm.WebAccount(
            user_id=user_id, email=email, normalized_email=normalized_email,
            password_hash=password_hash,
        )
        self._session.add(model)
        await self._session.flush()
        await self._session.refresh(model)
        return to_web_account(model)

    async def verify_email(self, user_id: UUID, verified_at: datetime):
        model = await self._session.get(orm.WebAccount, user_id)
        if model is None:
            raise RuntimeError("Web account disappeared")
        model.email_verified_at = verified_at
        await self._session.flush()
        await self._session.refresh(model)
        return to_web_account(model)

    async def update_password(self, user_id: UUID, password_hash: str):
        model = await self._session.get(orm.WebAccount, user_id)
        if model is None:
            raise RuntimeError("Web account disappeared")
        model.password_hash = password_hash
        model.failed_login_count = 0
        model.locked_until = None
        await self._session.flush()
        await self._session.refresh(model)
        return to_web_account(model)

    async def record_login_failure(
        self, user_id: UUID, *, failed_count: int, locked_until: datetime | None
    ) -> None:
        await self._session.execute(
            update(orm.WebAccount).where(orm.WebAccount.user_id == user_id).values(
                failed_login_count=failed_count, locked_until=locked_until
            )
        )

    async def clear_login_failures(self, user_id: UUID) -> None:
        await self._session.execute(
            update(orm.WebAccount).where(orm.WebAccount.user_id == user_id).values(
                failed_login_count=0, locked_until=None
            )
        )


class SqlAlchemyWebSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self, *, session_id: UUID, user_id: UUID, token_hash: str,
        created_at: datetime, expires_at: datetime
    ):
        model = orm.WebSession(
            id=session_id, user_id=user_id, token_hash=token_hash,
            created_at=created_at, expires_at=expires_at,
        )
        self._session.add(model)
        await self._session.flush()
        return to_web_session(model)

    async def get_active_by_hash(self, token_hash: str, now: datetime):
        model = await self._session.scalar(
            select(orm.WebSession).where(
                orm.WebSession.token_hash == token_hash,
                orm.WebSession.revoked_at.is_(None),
                orm.WebSession.expires_at > now,
            )
        )
        return to_web_session(model) if model is not None else None

    async def revoke_by_hash(self, token_hash: str, revoked_at: datetime) -> None:
        await self._session.execute(
            update(orm.WebSession).where(
                orm.WebSession.token_hash == token_hash,
                orm.WebSession.revoked_at.is_(None),
            ).values(revoked_at=revoked_at)
        )

    async def revoke_all_for_user(self, user_id: UUID, revoked_at: datetime) -> None:
        await self._session.execute(
            update(orm.WebSession).where(
                orm.WebSession.user_id == user_id,
                orm.WebSession.revoked_at.is_(None),
            ).values(revoked_at=revoked_at)
        )


class SqlAlchemyAuthTokenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def revoke_active(self, user_id: UUID, purpose: str, revoked_at: datetime) -> None:
        await self._session.execute(
            update(orm.AuthToken).where(
                orm.AuthToken.user_id == user_id,
                orm.AuthToken.purpose == purpose,
                orm.AuthToken.consumed_at.is_(None),
                orm.AuthToken.revoked_at.is_(None),
            ).values(revoked_at=revoked_at)
        )

    async def create(
        self, *, token_id: UUID, user_id: UUID, purpose: str, token_hash: str,
        created_at: datetime, expires_at: datetime
    ):
        model = orm.AuthToken(
            id=token_id, user_id=user_id, purpose=purpose, token_hash=token_hash,
            created_at=created_at, expires_at=expires_at,
        )
        self._session.add(model)
        await self._session.flush()
        return to_auth_token(model)

    async def get_for_update(self, token_hash: str):
        model = await self._session.scalar(
            select(orm.AuthToken).where(orm.AuthToken.token_hash == token_hash).with_for_update()
        )
        return to_auth_token(model) if model is not None else None

    async def consume(self, token_id: UUID, consumed_at: datetime) -> None:
        await self._session.execute(
            update(orm.AuthToken).where(orm.AuthToken.id == token_id).values(consumed_at=consumed_at)
        )


class SqlAlchemyTelegramLinkRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_user_lock(self, user_id: UUID) -> None:
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(f"telegram-link:{user_id}", 0)))
        )

    async def revoke_pending_for_user(self, user_id: UUID, now: datetime) -> None:
        await self._session.execute(
            update(orm.TelegramLinkRequest).where(
                orm.TelegramLinkRequest.user_id == user_id,
                orm.TelegramLinkRequest.status.in_(("pending_telegram", "pending_web_confirmation")),
            ).values(status="cancelled", cancelled_at=now)
        )

    async def create(
        self, *, request_id: UUID, user_id: UUID, token_hash: str,
        created_at: datetime, expires_at: datetime
    ):
        model = orm.TelegramLinkRequest(
            id=request_id, user_id=user_id, token_hash=token_hash,
            status="pending_telegram", created_at=created_at, expires_at=expires_at,
        )
        self._session.add(model)
        await self._session.flush()
        return to_telegram_link_request(model)

    async def get_by_id_for_user(self, request_id: UUID, user_id: UUID):
        model = await self._session.scalar(
            select(orm.TelegramLinkRequest).where(
                orm.TelegramLinkRequest.id == request_id,
                orm.TelegramLinkRequest.user_id == user_id,
            )
        )
        return to_telegram_link_request(model) if model is not None else None

    async def get_by_id_for_update(self, request_id: UUID, user_id: UUID):
        model = await self._session.scalar(
            select(orm.TelegramLinkRequest).where(
                orm.TelegramLinkRequest.id == request_id,
                orm.TelegramLinkRequest.user_id == user_id,
            ).with_for_update()
        )
        return to_telegram_link_request(model) if model is not None else None

    async def get_by_hash_for_update(self, token_hash: str):
        model = await self._session.scalar(
            select(orm.TelegramLinkRequest).where(
                orm.TelegramLinkRequest.token_hash == token_hash
            ).with_for_update()
        )
        return to_telegram_link_request(model) if model is not None else None

    async def set_candidate(
        self, request_id: UUID, *, telegram_user_id: str,
        username: str | None, display_name: str | None
    ):
        model = await self._session.get(orm.TelegramLinkRequest, request_id)
        if model is None:
            raise RuntimeError("Telegram link request disappeared")
        model.status = "pending_web_confirmation"
        model.candidate_telegram_user_id = telegram_user_id
        model.candidate_username = username
        model.candidate_display_name = display_name
        await self._session.flush()
        await self._session.refresh(model)
        return to_telegram_link_request(model)

    async def complete(self, request_id: UUID, completed_at: datetime):
        model = await self._session.get(orm.TelegramLinkRequest, request_id)
        if model is None:
            raise RuntimeError("Telegram link request disappeared")
        model.status = "completed"
        model.completed_at = completed_at
        await self._session.flush()
        await self._session.refresh(model)
        return to_telegram_link_request(model)

    async def cancel(self, request_id: UUID, cancelled_at: datetime) -> None:
        await self._session.execute(
            update(orm.TelegramLinkRequest).where(
                orm.TelegramLinkRequest.id == request_id,
                orm.TelegramLinkRequest.status.in_(("pending_telegram", "pending_web_confirmation")),
            ).values(status="cancelled", cancelled_at=cancelled_at)
        )


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

    async def list_for_user(self, user_id: UUID) -> tuple[Exercise, ...]:
        models = await self._session.scalars(
            select(orm.Exercise)
            .where(orm.Exercise.user_id == user_id)
            .order_by(orm.Exercise.name, orm.Exercise.id)
        )
        return tuple(to_exercise(model) for model in models)

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
        program_workout_id: UUID | None = None,
    ) -> Workout:
        statement = (
            insert(orm.Workout)
            .values(
                id=workout_id,
                user_id=user_id,
                performed_on=performed_on,
                notes=notes,
                program_workout_id=program_workout_id,
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
        log_batch_id: UUID,
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
            log_batch_id=log_batch_id,
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

    async def delete(self, workout_id: UUID, user_id: UUID) -> None:
        await self._session.execute(
            delete(orm.Workout).where(
                orm.Workout.id == workout_id,
                orm.Workout.user_id == user_id,
                orm.Workout.status == "draft",
            )
        )
        await self._session.flush()

    async def delete_last_log_batch(
        self, workout_id: UUID, user_id: UUID
    ) -> tuple[WorkoutExercise, ...]:
        latest_batch_id = await self._session.scalar(
            select(orm.WorkoutExercise.log_batch_id)
            .where(
                orm.WorkoutExercise.workout_id == workout_id,
                orm.WorkoutExercise.user_id == user_id,
            )
            .order_by(orm.WorkoutExercise.position.desc())
            .limit(1)
        )
        if latest_batch_id is None:
            return ()
        models = tuple(
            await self._session.scalars(
                select(orm.WorkoutExercise)
                .where(
                    orm.WorkoutExercise.workout_id == workout_id,
                    orm.WorkoutExercise.user_id == user_id,
                    orm.WorkoutExercise.log_batch_id == latest_batch_id,
                )
                .options(
                    selectinload(orm.WorkoutExercise.exercise),
                    selectinload(orm.WorkoutExercise.sets),
                )
                .order_by(orm.WorkoutExercise.position)
            )
        )
        removed = tuple(to_workout_exercise(model) for model in models)
        await self._session.execute(
            delete(orm.WorkoutExercise).where(
                orm.WorkoutExercise.workout_id == workout_id,
                orm.WorkoutExercise.user_id == user_id,
                orm.WorkoutExercise.log_batch_id == latest_batch_id,
            )
        )
        await self._session.flush()
        self._session.expire_all()
        return removed

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

    async def list_completed(
        self,
        user_id: UUID,
        *,
        limit: int,
        after: HistoryCursor | None,
    ) -> tuple[Workout, ...]:
        statement = (
            select(orm.Workout)
            .where(
                orm.Workout.user_id == user_id,
                orm.Workout.status == "completed",
            )
            .options(*_workout_options())
            .order_by(
                orm.Workout.performed_on.desc(),
                orm.Workout.created_at.desc(),
                orm.Workout.id.desc(),
            )
            .limit(limit)
        )
        models = await self._session.scalars(_after_history_cursor(statement, after))
        return tuple(to_workout(model) for model in models)

    async def list_completed_for_exercise(
        self,
        user_id: UUID,
        exercise_id: UUID,
        *,
        limit: int,
        after: HistoryCursor | None,
    ) -> tuple[ExerciseHistoryItem, ...]:
        statement = (
            select(orm.Workout)
            .where(
                orm.Workout.user_id == user_id,
                orm.Workout.status == "completed",
                orm.Workout.exercises.any(
                    orm.WorkoutExercise.exercise_id == exercise_id
                ),
            )
            .options(*_exercise_history_options(exercise_id))
            .order_by(
                orm.Workout.performed_on.desc(),
                orm.Workout.created_at.desc(),
                orm.Workout.id.desc(),
            )
            .limit(limit)
        )
        models = await self._session.scalars(_after_history_cursor(statement, after))
        return tuple(
            ExerciseHistoryItem(
                workout_id=model.id,
                performed_on=model.performed_on,
                workout_notes=model.notes,
                workout_created_at=model.created_at,
                occurrences=tuple(
                    to_workout_exercise(occurrence) for occurrence in model.exercises
                ),
            )
            for model in models
        )

    async def latest_completed_for_exercises(
        self, user_id: UUID, exercise_ids: tuple[UUID, ...]
    ) -> dict[UUID, ExerciseHistoryItem]:
        if not exercise_ids:
            return {}
        models = tuple(
            await self._session.scalars(
                select(orm.Workout)
                .where(
                    orm.Workout.user_id == user_id,
                    orm.Workout.status == "completed",
                    orm.Workout.exercises.any(
                        orm.WorkoutExercise.exercise_id.in_(exercise_ids)
                    ),
                )
                .options(*_workout_options())
                .order_by(
                    orm.Workout.performed_on.desc(),
                    orm.Workout.created_at.desc(),
                    orm.Workout.id.desc(),
                )
            )
        )
        result: dict[UUID, ExerciseHistoryItem] = {}
        wanted = set(exercise_ids)
        for model in models:
            by_exercise: dict[UUID, list[WorkoutExercise]] = {}
            for occurrence in model.exercises:
                exercise_id = occurrence.exercise.id
                if exercise_id in wanted and exercise_id not in result:
                    by_exercise.setdefault(exercise_id, []).append(
                        to_workout_exercise(occurrence)
                    )
            for exercise_id, occurrences in by_exercise.items():
                result[exercise_id] = ExerciseHistoryItem(
                    workout_id=model.id,
                    performed_on=model.performed_on,
                    workout_notes=model.notes,
                    workout_created_at=model.created_at,
                    occurrences=tuple(occurrences),
                )
            if len(result) == len(wanted):
                break
        return result


class SqlAlchemyStatisticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_completed_sets(
        self,
        user_id: UUID,
        *,
        from_date: date | None,
        to_date: date,
        exercise_id: UUID | None = None,
    ) -> tuple[StatisticsSetRecord, ...]:
        statement = (
            select(
                orm.Workout.id,
                orm.Workout.performed_on,
                orm.Workout.created_at,
                orm.Exercise.id,
                orm.Exercise.name,
                orm.PerformedSet.repetitions,
                orm.PerformedSet.load_kg,
            )
            .join(orm.WorkoutExercise, orm.WorkoutExercise.workout_id == orm.Workout.id)
            .join(orm.Exercise, orm.Exercise.id == orm.WorkoutExercise.exercise_id)
            .join(
                orm.PerformedSet,
                orm.PerformedSet.workout_exercise_id == orm.WorkoutExercise.id,
            )
            .where(
                orm.Workout.user_id == user_id,
                orm.Workout.status == "completed",
                orm.Workout.performed_on <= to_date,
            )
            .order_by(
                orm.Workout.performed_on,
                orm.Workout.created_at,
                orm.Workout.id,
                orm.WorkoutExercise.position,
                orm.PerformedSet.set_number,
            )
        )
        if from_date is not None:
            statement = statement.where(orm.Workout.performed_on >= from_date)
        if exercise_id is not None:
            statement = statement.where(orm.WorkoutExercise.exercise_id == exercise_id)
        rows = (await self._session.execute(statement)).all()
        return tuple(
            StatisticsSetRecord(
                workout_id=row[0],
                performed_on=row[1],
                workout_created_at=row[2],
                exercise_id=row[3],
                exercise_name=row[4],
                repetitions=row[5],
                load_kg=row[6],
            )
            for row in rows
        )


class SqlAlchemyProgramWorkoutRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def acquire_user_lock(self, user_id: UUID) -> None:
        await self._session.execute(
            select(func.pg_advisory_xact_lock(func.hashtextextended(str(user_id), 17)))
        )

    async def _load(self, program_workout_id: UUID, user_id: UUID) -> orm.ProgramWorkout | None:
        return await self._session.scalar(
            select(orm.ProgramWorkout)
            .where(orm.ProgramWorkout.id == program_workout_id, orm.ProgramWorkout.user_id == user_id)
            .options(*_program_options())
        )

    async def list_active(self, user_id: UUID) -> tuple[ProgramWorkout, ...]:
        models = await self._session.scalars(
            select(orm.ProgramWorkout)
            .where(orm.ProgramWorkout.user_id == user_id, orm.ProgramWorkout.deactivated_at.is_(None))
            .options(*_program_options())
            .order_by(orm.ProgramWorkout.day_number, orm.ProgramWorkout.created_at)
        )
        return tuple(to_program_workout(model) for model in models)

    async def get_by_id(self, program_workout_id: UUID, user_id: UUID) -> ProgramWorkout | None:
        model = await self._load(program_workout_id, user_id)
        return to_program_workout(model) if model is not None else None

    async def get_active_by_selector(self, user_id: UUID, selector: str) -> ProgramWorkout | None:
        try:
            number = int(selector)
        except ValueError:
            number = None
        condition = (
            orm.ProgramWorkout.day_number == number
            if number is not None
            else orm.ProgramWorkout.normalized_alias == selector
        )
        model = await self._session.scalar(
            select(orm.ProgramWorkout)
            .where(
                orm.ProgramWorkout.user_id == user_id,
                orm.ProgramWorkout.deactivated_at.is_(None),
                condition,
            )
            .options(*_program_options())
        )
        return to_program_workout(model) if model is not None else None

    async def deactivate_all(self, user_id: UUID) -> int:
        result = await self._session.execute(
            update(orm.ProgramWorkout)
            .where(orm.ProgramWorkout.user_id == user_id, orm.ProgramWorkout.deactivated_at.is_(None))
            .values(deactivated_at=func.now())
        )
        await self._session.flush()
        return int(result.rowcount or 0)

    async def deactivate(self, program_workout_id: UUID, user_id: UUID) -> None:
        await self._session.execute(
            update(orm.ProgramWorkout)
            .where(
                orm.ProgramWorkout.id == program_workout_id,
                orm.ProgramWorkout.user_id == user_id,
                orm.ProgramWorkout.deactivated_at.is_(None),
            )
            .values(deactivated_at=func.now())
        )
        await self._session.flush()

    async def create(
        self, *, program_workout_id: UUID, user_id: UUID, day_number: int,
        alias: str, normalized_alias: str, notes: str | None,
        items: tuple[tuple[UUID, str, str, UUID | None, int, int, int | None], ...],
    ) -> ProgramWorkout:
        model = orm.ProgramWorkout(
            id=program_workout_id, user_id=user_id, day_number=day_number,
            alias=alias, normalized_alias=normalized_alias, notes=notes,
        )
        model.items = [
            orm.ProgramWorkoutItem(
                id=item_id, user_id=user_id, position=position,
                exercise_name=name, normalized_exercise_name=normalized_name,
                exercise_id=exercise_id, target_sets=target_sets,
                target_repetitions=target_repetitions, rest_seconds=rest_seconds,
            )
            for position, (item_id, name, normalized_name, exercise_id, target_sets, target_repetitions, rest_seconds)
            in enumerate(items, start=1)
        ]
        self._session.add(model)
        await self._session.flush()
        loaded = await self._load(program_workout_id, user_id)
        if loaded is None:
            raise RuntimeError("Created program workout could not be loaded")
        return to_program_workout(loaded)


class SqlAlchemyWorkoutLogClarificationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _get(
        self, clarification_id: UUID, user_id: UUID, *, for_update: bool = False
    ) -> orm.WorkoutLogClarification | None:
        statement = select(orm.WorkoutLogClarification).where(
            orm.WorkoutLogClarification.id == clarification_id,
            orm.WorkoutLogClarification.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def create(
        self,
        *,
        clarification_id: UUID,
        user_id: UUID,
        workout_id: UUID,
        original_text: str,
        clarification_message: str,
        model: str,
        initial_prompt_version: str,
        followup_prompt_version: str,
        created_at: datetime,
        expires_at: datetime,
    ) -> WorkoutLogClarification:
        inserted_id = await self._session.scalar(
            insert(orm.WorkoutLogClarification)
            .values(
                id=clarification_id,
                user_id=user_id,
                workout_id=workout_id,
                status="pending",
                original_text=original_text,
                clarification_message=clarification_message,
                model=model,
                initial_prompt_version=initial_prompt_version,
                followup_prompt_version=followup_prompt_version,
                created_at=created_at,
                expires_at=expires_at,
            )
            .on_conflict_do_nothing(
                index_elements=["user_id", "workout_id"],
                index_where=orm.WorkoutLogClarification.status == "pending",
            )
            .returning(orm.WorkoutLogClarification.id)
        )
        if inserted_id is None:
            existing = await self.get_pending_for_workout(user_id, workout_id)
            if existing is None:
                raise RuntimeError("Pending clarification conflict did not return a row")
            return existing
        model_value = await self._get(inserted_id, user_id)
        if model_value is None:
            raise RuntimeError("Created clarification could not be loaded")
        return to_workout_log_clarification(model_value)

    async def get_by_id(
        self, clarification_id: UUID, user_id: UUID
    ) -> WorkoutLogClarification | None:
        model = await self._get(clarification_id, user_id)
        return to_workout_log_clarification(model) if model is not None else None

    async def get_for_update(
        self, clarification_id: UUID, user_id: UUID
    ) -> WorkoutLogClarification | None:
        model = await self._get(clarification_id, user_id, for_update=True)
        return to_workout_log_clarification(model) if model is not None else None

    async def get_pending_for_workout(
        self, user_id: UUID, workout_id: UUID
    ) -> WorkoutLogClarification | None:
        model = await self._session.scalar(
            select(orm.WorkoutLogClarification).where(
                orm.WorkoutLogClarification.user_id == user_id,
                orm.WorkoutLogClarification.workout_id == workout_id,
                orm.WorkoutLogClarification.status == "pending",
            )
        )
        return to_workout_log_clarification(model) if model is not None else None

    async def finish(
        self,
        clarification_id: UUID,
        user_id: UUID,
        *,
        status: str,
        terminal_at: datetime,
    ) -> WorkoutLogClarification:
        updated_id = await self._session.scalar(
            update(orm.WorkoutLogClarification)
            .where(
                orm.WorkoutLogClarification.id == clarification_id,
                orm.WorkoutLogClarification.user_id == user_id,
                orm.WorkoutLogClarification.status == "pending",
            )
            .values(
                status=status,
                original_text=None,
                clarification_message=None,
                terminal_at=terminal_at,
            )
            .returning(orm.WorkoutLogClarification.id)
        )
        if updated_id is None:
            existing = await self.get_by_id(clarification_id, user_id)
            if existing is None:
                raise RuntimeError("Clarification not found")
            return existing
        model = await self._get(updated_id, user_id)
        if model is None:
            raise RuntimeError("Finished clarification could not be loaded")
        return to_workout_log_clarification(model)

    async def cancel_pending_for_workout(
        self,
        user_id: UUID,
        workout_id: UUID,
        *,
        terminal_at: datetime,
    ) -> int:
        result = await self._session.execute(
            update(orm.WorkoutLogClarification)
            .where(
                orm.WorkoutLogClarification.user_id == user_id,
                orm.WorkoutLogClarification.workout_id == workout_id,
                orm.WorkoutLogClarification.status == "pending",
            )
            .values(
                status="cancelled",
                original_text=None,
                clarification_message=None,
                terminal_at=terminal_at,
            )
        )
        return int(result.rowcount or 0)


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
