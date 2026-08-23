"""Read-only training statistics and progress projections."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID
from zoneinfo import ZoneInfo

from app.application.ports import UnitOfWorkFactory
from app.domain.exceptions import NotFoundError
from app.domain.models import Exercise, LoadUnit


class StatisticsPeriod(StrEnum):
    FOUR_WEEKS = "4w"
    TWELVE_WEEKS = "12w"
    ONE_YEAR = "1y"
    ALL = "all"


@dataclass(frozen=True, slots=True)
class StatisticsWindow:
    period: StatisticsPeriod
    from_date: date | None
    to_date: date


@dataclass(frozen=True, slots=True)
class StatisticsSetRecord:
    workout_id: UUID
    performed_on: date
    workout_created_at: datetime
    exercise_id: UUID
    exercise_name: str
    repetitions: int
    load_kg: Decimal | None


@dataclass(frozen=True, slots=True)
class ExerciseSessionStatistics:
    workout_id: UUID
    performed_on: date
    workout_created_at: datetime
    set_count: int
    repetition_count: int
    max_set_repetitions: int
    top_load_kg: Decimal | None
    estimated_one_rep_max_kg: Decimal | None
    external_volume_kg: Decimal | None


@dataclass(frozen=True, slots=True)
class ExerciseStatisticsSummary:
    session_count: int
    set_count: int
    repetition_count: int
    max_set_repetitions: int
    best_load_kg: Decimal | None
    best_estimated_one_rep_max_kg: Decimal | None
    best_session_volume_kg: Decimal | None


@dataclass(frozen=True, slots=True)
class ExerciseStatisticsResult:
    exercise: Exercise
    preferred_load_unit: LoadUnit
    window: StatisticsWindow
    summary: ExerciseStatisticsSummary
    series: tuple[ExerciseSessionStatistics, ...]


@dataclass(frozen=True, slots=True)
class OverviewTotals:
    workout_count: int
    active_day_count: int
    set_count: int
    repetition_count: int
    external_volume_kg: Decimal | None


@dataclass(frozen=True, slots=True)
class OverviewBucket:
    period_start: date
    workout_count: int
    set_count: int
    repetition_count: int
    external_volume_kg: Decimal | None


@dataclass(frozen=True, slots=True)
class TopExercise:
    exercise_id: UUID
    exercise_name: str
    workout_count: int
    set_count: int


@dataclass(frozen=True, slots=True)
class EstimatedOneRepMaxRecord:
    exercise_id: UUID
    exercise_name: str
    workout_id: UUID
    performed_on: date
    estimated_one_rep_max_kg: Decimal
    previous_best_kg: Decimal


@dataclass(frozen=True, slots=True)
class OverviewStatisticsResult:
    preferred_load_unit: LoadUnit
    window: StatisticsWindow
    bucket: str
    current: OverviewTotals
    previous: OverviewTotals | None
    series: tuple[OverviewBucket, ...]
    top_exercises: tuple[TopExercise, ...]
    recent_records: tuple[EstimatedOneRepMaxRecord, ...]


_PERIOD_DAYS = {
    StatisticsPeriod.FOUR_WEEKS: 28,
    StatisticsPeriod.TWELVE_WEEKS: 84,
    StatisticsPeriod.ONE_YEAR: 365,
}


def statistics_window(period: StatisticsPeriod, today: date) -> StatisticsWindow:
    days = _PERIOD_DAYS.get(period)
    return StatisticsWindow(
        period=period,
        from_date=today - timedelta(days=days - 1) if days is not None else None,
        to_date=today,
    )


def estimated_one_rep_max(load_kg: Decimal | None, repetitions: int) -> Decimal | None:
    if load_kg is None or repetitions < 1 or repetitions > 12:
        return None
    if repetitions == 1:
        return load_kg
    return load_kg * (Decimal("1") + Decimal(repetitions) / Decimal("30"))


def _session_statistics(
    records: tuple[StatisticsSetRecord, ...],
) -> tuple[ExerciseSessionStatistics, ...]:
    grouped: dict[UUID, list[StatisticsSetRecord]] = defaultdict(list)
    for record in records:
        grouped[record.workout_id].append(record)
    result: list[ExerciseSessionStatistics] = []
    for values in grouped.values():
        loads = [item.load_kg for item in values if item.load_kg is not None]
        estimates = [
            estimate
            for item in values
            if (estimate := estimated_one_rep_max(item.load_kg, item.repetitions))
            is not None
        ]
        volume = (
            sum(
                (item.load_kg * item.repetitions for item in values if item.load_kg is not None),
                Decimal("0"),
            )
            if loads
            else None
        )
        first = values[0]
        result.append(
            ExerciseSessionStatistics(
                workout_id=first.workout_id,
                performed_on=first.performed_on,
                workout_created_at=first.workout_created_at,
                set_count=len(values),
                repetition_count=sum(item.repetitions for item in values),
                max_set_repetitions=max(item.repetitions for item in values),
                top_load_kg=max(loads) if loads else None,
                estimated_one_rep_max_kg=max(estimates) if estimates else None,
                external_volume_kg=volume,
            )
        )
    return tuple(
        sorted(result, key=lambda item: (item.performed_on, item.workout_created_at, item.workout_id))
    )


def _totals(records: tuple[StatisticsSetRecord, ...]) -> OverviewTotals:
    loaded = [item for item in records if item.load_kg is not None]
    return OverviewTotals(
        workout_count=len({item.workout_id for item in records}),
        active_day_count=len({item.performed_on for item in records}),
        set_count=len(records),
        repetition_count=sum(item.repetitions for item in records),
        external_volume_kg=(
            sum((item.load_kg * item.repetitions for item in loaded), Decimal("0"))
            if loaded
            else None
        ),
    )


def _month_start(value: date) -> date:
    return value.replace(day=1)


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), value.month % 12 + 1, 1)


def _bucket_series(
    records: tuple[StatisticsSetRecord, ...], window: StatisticsWindow
) -> tuple[str, tuple[OverviewBucket, ...]]:
    bucket_kind = "week" if window.period in {
        StatisticsPeriod.FOUR_WEEKS,
        StatisticsPeriod.TWELVE_WEEKS,
    } else "month"
    if window.from_date is None and not records:
        return bucket_kind, ()
    first_date = window.from_date or min(item.performed_on for item in records)
    bucket_start = (
        first_date - timedelta(days=first_date.weekday())
        if bucket_kind == "week"
        else _month_start(first_date)
    )
    end_bucket = (
        window.to_date - timedelta(days=window.to_date.weekday())
        if bucket_kind == "week"
        else _month_start(window.to_date)
    )
    grouped: dict[date, list[StatisticsSetRecord]] = defaultdict(list)
    for item in records:
        key = (
            item.performed_on - timedelta(days=item.performed_on.weekday())
            if bucket_kind == "week"
            else _month_start(item.performed_on)
        )
        grouped[key].append(item)
    result: list[OverviewBucket] = []
    current = bucket_start
    while current <= end_bucket:
        values = tuple(grouped.get(current, ()))
        totals = _totals(values)
        result.append(OverviewBucket(
            period_start=current,
            workout_count=totals.workout_count,
            set_count=totals.set_count,
            repetition_count=totals.repetition_count,
            external_volume_kg=totals.external_volume_kg,
        ))
        current = current + timedelta(days=7) if bucket_kind == "week" else _next_month(current)
    return bucket_kind, tuple(result)


class GetExerciseStatistics:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        *,
        user_id: UUID,
        exercise_id: UUID,
        period: StatisticsPeriod,
        today: date | None = None,
    ) -> ExerciseStatisticsResult:
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError("User not found")
            exercise = await uow.exercises.get_by_id(exercise_id, user_id)
            if exercise is None:
                raise NotFoundError("Exercise not found")
            local_today = today or datetime.now(ZoneInfo(user.timezone)).date()
            window = statistics_window(period, local_today)
            records = await uow.statistics.list_completed_sets(
                user_id,
                from_date=window.from_date,
                to_date=window.to_date,
                exercise_id=exercise_id,
            )
        series = _session_statistics(records)
        summary = ExerciseStatisticsSummary(
            session_count=len(series),
            set_count=sum(item.set_count for item in series),
            repetition_count=sum(item.repetition_count for item in series),
            max_set_repetitions=max((item.max_set_repetitions for item in series), default=0),
            best_load_kg=max((item.top_load_kg for item in series if item.top_load_kg is not None), default=None),
            best_estimated_one_rep_max_kg=max(
                (item.estimated_one_rep_max_kg for item in series if item.estimated_one_rep_max_kg is not None),
                default=None,
            ),
            best_session_volume_kg=max(
                (item.external_volume_kg for item in series if item.external_volume_kg is not None),
                default=None,
            ),
        )
        return ExerciseStatisticsResult(
            exercise=exercise,
            preferred_load_unit=user.preferred_load_unit,
            window=window,
            summary=summary,
            series=series,
        )


class GetOverviewStatistics:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        *,
        user_id: UUID,
        period: StatisticsPeriod,
        today: date | None = None,
    ) -> OverviewStatisticsResult:
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_id(user_id)
            if user is None:
                raise NotFoundError("User not found")
            local_today = today or datetime.now(ZoneInfo(user.timezone)).date()
            window = statistics_window(period, local_today)
            all_records = await uow.statistics.list_completed_sets(
                user_id, from_date=None, to_date=window.to_date
            )

        current_records = tuple(
            item for item in all_records
            if window.from_date is None or item.performed_on >= window.from_date
        )
        previous = None
        if window.from_date is not None:
            days = _PERIOD_DAYS[period]
            previous_to = window.from_date - timedelta(days=1)
            previous_from = previous_to - timedelta(days=days - 1)
            previous = _totals(tuple(
                item for item in all_records
                if previous_from <= item.performed_on <= previous_to
            ))

        bucket, series = _bucket_series(current_records, window)
        by_exercise: dict[UUID, list[StatisticsSetRecord]] = defaultdict(list)
        for item in current_records:
            by_exercise[item.exercise_id].append(item)
        top_exercises = tuple(sorted(
            (
                TopExercise(
                    exercise_id=exercise_id,
                    exercise_name=values[0].exercise_name,
                    workout_count=len({item.workout_id for item in values}),
                    set_count=len(values),
                )
                for exercise_id, values in by_exercise.items()
            ),
            key=lambda item: (-item.workout_count, -item.set_count, item.exercise_name.casefold()),
        )[:5])

        sessions: dict[tuple[UUID, UUID], list[StatisticsSetRecord]] = defaultdict(list)
        for item in all_records:
            sessions[(item.workout_id, item.exercise_id)].append(item)
        best_by_exercise: dict[UUID, Decimal] = {}
        records: list[tuple[datetime, UUID, EstimatedOneRepMaxRecord]] = []
        ordered_sessions = sorted(
            sessions.values(),
            key=lambda values: (values[0].performed_on, values[0].workout_created_at, values[0].workout_id),
        )
        for values in ordered_sessions:
            estimates = [
                estimate for item in values
                if (estimate := estimated_one_rep_max(item.load_kg, item.repetitions)) is not None
            ]
            if not estimates:
                continue
            current_best = max(estimates)
            first = values[0]
            previous_best = best_by_exercise.get(first.exercise_id)
            if (
                previous_best is not None
                and current_best > previous_best
                and (window.from_date is None or first.performed_on >= window.from_date)
            ):
                records.append((
                    first.workout_created_at,
                    first.workout_id,
                    EstimatedOneRepMaxRecord(
                        exercise_id=first.exercise_id,
                        exercise_name=first.exercise_name,
                        workout_id=first.workout_id,
                        performed_on=first.performed_on,
                        estimated_one_rep_max_kg=current_best,
                        previous_best_kg=previous_best,
                    ),
                ))
            best_by_exercise[first.exercise_id] = max(current_best, previous_best or current_best)

        recent_records = tuple(
            item[2] for item in sorted(records, key=lambda item: (item[2].performed_on, item[0], item[1]), reverse=True)[:5]
        )
        return OverviewStatisticsResult(
            preferred_load_unit=user.preferred_load_unit,
            window=window,
            bucket=bucket,
            current=_totals(current_records),
            previous=previous,
            series=series,
            top_exercises=top_exercises,
            recent_records=recent_records,
        )
