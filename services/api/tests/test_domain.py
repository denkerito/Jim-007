from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.exceptions import InvalidWorkoutStateError
from app.domain.models import (
    Exercise,
    Load,
    LoadUnit,
    PerformedSet,
    User,
    Workout,
    WorkoutExercise,
    WorkoutStatus,
)
from app.domain.normalization import clean_required_text, normalize_exercise_name


NOW = datetime(2026, 8, 3, tzinfo=timezone.utc)


def make_exercise(user_id=None) -> Exercise:
    owner = user_id or uuid4()
    return Exercise(
        id=uuid4(),
        user_id=owner,
        name="Bench Press",
        normalized_name="bench press",
        created_at=NOW,
    )


def make_occurrence(user_id) -> WorkoutExercise:
    return WorkoutExercise(
        id=uuid4(),
        exercise=make_exercise(user_id),
        position=1,
        sets=(
            PerformedSet(
                id=uuid4(), set_number=1, repetitions=8, load=Load(value=80, unit="kg")
            ),
        ),
    )


def test_load_converts_lb_deterministically() -> None:
    load = Load(value=Decimal("100"), unit=LoadUnit.LB)
    assert load.kilograms == Decimal("45.359237")


def test_domain_models_are_frozen() -> None:
    load = Load(value=Decimal("80"), unit=LoadUnit.KG)
    with pytest.raises(ValidationError):
        load.value = Decimal("90")  # type: ignore[misc]


def test_normalization_is_unicode_and_whitespace_stable() -> None:
    assert clean_required_text("  Bench\t  Press ") == "Bench Press"
    assert normalize_exercise_name("  ＢＥＮＣＨ\tPress ") == "bench press"


def test_workout_requires_exercise_before_completion() -> None:
    workout = Workout(
        id=uuid4(),
        user_id=uuid4(),
        performed_on=date(2026, 8, 3),
        status=WorkoutStatus.DRAFT,
        created_at=NOW,
    )
    with pytest.raises(InvalidWorkoutStateError):
        workout.as_completed(NOW)


def test_workout_validates_positions_and_ownership() -> None:
    user_id = uuid4()
    occurrence = make_occurrence(user_id)
    workout = Workout(
        id=uuid4(),
        user_id=user_id,
        performed_on=date(2026, 8, 3),
        status="draft",
        created_at=NOW,
        exercises=(occurrence,),
    )
    completed = workout.as_completed(NOW)
    assert completed.status is WorkoutStatus.COMPLETED
    assert completed.exercises == (occurrence,)


def test_user_rejects_unknown_timezone() -> None:
    with pytest.raises(ValidationError):
        User(
            id=uuid4(),
            locale="it-IT",
            timezone="Mars/Olympus_Mons",
            preferred_load_unit="kg",
            created_at=NOW,
            updated_at=NOW,
        )
