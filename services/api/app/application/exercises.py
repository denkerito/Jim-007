"""Transport-independent exercise catalog use cases."""

from dataclasses import dataclass
from uuid import UUID, uuid4

from app.application.ports import UnitOfWork, UnitOfWorkFactory
from app.domain.exceptions import InvalidExerciseNameError, NotFoundError
from app.domain.models import Exercise
from app.domain.normalization import clean_required_text, normalize_exercise_name


MAX_EXERCISE_NAME_LENGTH = 255


@dataclass(frozen=True, slots=True)
class CreateExerciseResult:
    exercise: Exercise
    created: bool


def clean_exercise_name(name: str) -> str:
    """Return a display name valid for every exercise-creation adapter."""

    cleaned = clean_required_text(name)
    if not cleaned:
        raise InvalidExerciseNameError("Exercise name must not be blank")
    if len(cleaned) > MAX_EXERCISE_NAME_LENGTH:
        raise InvalidExerciseNameError(
            f"Exercise name must not exceed {MAX_EXERCISE_NAME_LENGTH} characters"
        )
    return cleaned


async def create_or_get_exercise(
    *,
    uow: UnitOfWork,
    user_id: UUID,
    name: str,
) -> CreateExerciseResult:
    """Create an exercise atomically or resolve its normalized duplicate."""

    cleaned = clean_exercise_name(name)
    exercise, created = await uow.exercises.get_or_create(
        exercise_id=uuid4(),
        user_id=user_id,
        name=cleaned,
        normalized_name=normalize_exercise_name(cleaned),
    )
    return CreateExerciseResult(exercise=exercise, created=created)


class CreateExercise:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, *, user_id: UUID, name: str) -> CreateExerciseResult:
        async with self._uow_factory() as uow:
            if await uow.users.get_by_id(user_id) is None:
                raise NotFoundError("User not found")
            result = await create_or_get_exercise(
                uow=uow,
                user_id=user_id,
                name=name,
            )
            await uow.commit()
            return result


class RenameExercise:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self, *, user_id: UUID, exercise_id: UUID, name: str
    ) -> Exercise:
        cleaned = clean_exercise_name(name)
        async with self._uow_factory() as uow:
            exercise = await uow.exercises.rename(
                exercise_id=exercise_id,
                user_id=user_id,
                name=cleaned,
                normalized_name=normalize_exercise_name(cleaned),
            )
            if exercise is None:
                raise NotFoundError("Exercise not found")
            await uow.commit()
            return exercise
