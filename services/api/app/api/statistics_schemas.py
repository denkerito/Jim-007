"""HTTP schemas for web training statistics."""

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from app.api.schemas import ApiModel, ExerciseResponse
from app.application.statistics import (
    ExerciseStatisticsResult,
    OverviewStatisticsResult,
    OverviewTotals,
    StatisticsPeriod,
)
from app.domain.models import LoadUnit


_THREE_PLACES = Decimal("0.001")
_LB_IN_KG = Decimal("0.45359237")


class StatisticsWindowResponse(ApiModel):
    period: StatisticsPeriod
    from_date: date | None
    to_date: date


class MassMetricResponse(ApiModel):
    value: Decimal
    unit: LoadUnit
    kilograms: Decimal


class VolumeMetricResponse(ApiModel):
    value: Decimal
    load_unit: LoadUnit
    kilogram_repetitions: Decimal


class ExerciseStatisticsSummaryResponse(ApiModel):
    session_count: int
    set_count: int
    repetition_count: int
    max_set_repetitions: int
    best_load: MassMetricResponse | None
    best_estimated_one_rep_max: MassMetricResponse | None
    best_session_volume: VolumeMetricResponse | None


class ExerciseStatisticsPointResponse(ApiModel):
    workout_id: UUID
    performed_on: date
    set_count: int
    repetition_count: int
    max_set_repetitions: int
    top_load: MassMetricResponse | None
    estimated_one_rep_max: MassMetricResponse | None
    external_volume: VolumeMetricResponse | None


class ExerciseStatisticsResponse(ApiModel):
    exercise: ExerciseResponse
    period: StatisticsWindowResponse
    summary: ExerciseStatisticsSummaryResponse
    series: tuple[ExerciseStatisticsPointResponse, ...]


class OverviewTotalsResponse(ApiModel):
    workout_count: int
    active_day_count: int
    set_count: int
    repetition_count: int
    external_volume: VolumeMetricResponse | None


class OverviewBucketResponse(ApiModel):
    period_start: date
    workout_count: int
    set_count: int
    repetition_count: int
    external_volume: VolumeMetricResponse | None


class TopExerciseResponse(ApiModel):
    exercise_id: UUID
    exercise_name: str
    workout_count: int
    set_count: int


class EstimatedOneRepMaxRecordResponse(ApiModel):
    exercise_id: UUID
    exercise_name: str
    workout_id: UUID
    performed_on: date
    estimated_one_rep_max: MassMetricResponse
    previous_best: MassMetricResponse


class OverviewStatisticsResponse(ApiModel):
    period: StatisticsWindowResponse
    bucket: str
    current: OverviewTotalsResponse
    previous: OverviewTotalsResponse | None
    series: tuple[OverviewBucketResponse, ...]
    top_exercises: tuple[TopExerciseResponse, ...]
    recent_records: tuple[EstimatedOneRepMaxRecordResponse, ...]


def _mass(value_kg: Decimal | None, unit: LoadUnit) -> MassMetricResponse | None:
    if value_kg is None:
        return None
    value = value_kg if unit is LoadUnit.KG else value_kg / _LB_IN_KG
    return MassMetricResponse(
        value=value.quantize(_THREE_PLACES, rounding=ROUND_HALF_UP),
        unit=unit,
        kilograms=value_kg.quantize(_THREE_PLACES, rounding=ROUND_HALF_UP),
    )


def _volume(value_kg: Decimal | None, unit: LoadUnit) -> VolumeMetricResponse | None:
    if value_kg is None:
        return None
    value = value_kg if unit is LoadUnit.KG else value_kg / _LB_IN_KG
    return VolumeMetricResponse(
        value=value.quantize(_THREE_PLACES, rounding=ROUND_HALF_UP),
        load_unit=unit,
        kilogram_repetitions=value_kg.quantize(_THREE_PLACES, rounding=ROUND_HALF_UP),
    )


def _window(value) -> StatisticsWindowResponse:
    return StatisticsWindowResponse(
        period=value.period, from_date=value.from_date, to_date=value.to_date
    )


def exercise_statistics_response(value: ExerciseStatisticsResult) -> ExerciseStatisticsResponse:
    unit = value.preferred_load_unit
    return ExerciseStatisticsResponse(
        exercise=ExerciseResponse(
            id=value.exercise.id,
            name=value.exercise.name,
            normalized_name=value.exercise.normalized_name,
        ),
        period=_window(value.window),
        summary=ExerciseStatisticsSummaryResponse(
            session_count=value.summary.session_count,
            set_count=value.summary.set_count,
            repetition_count=value.summary.repetition_count,
            max_set_repetitions=value.summary.max_set_repetitions,
            best_load=_mass(value.summary.best_load_kg, unit),
            best_estimated_one_rep_max=_mass(
                value.summary.best_estimated_one_rep_max_kg, unit
            ),
            best_session_volume=_volume(value.summary.best_session_volume_kg, unit),
        ),
        series=tuple(
            ExerciseStatisticsPointResponse(
                workout_id=item.workout_id,
                performed_on=item.performed_on,
                set_count=item.set_count,
                repetition_count=item.repetition_count,
                max_set_repetitions=item.max_set_repetitions,
                top_load=_mass(item.top_load_kg, unit),
                estimated_one_rep_max=_mass(item.estimated_one_rep_max_kg, unit),
                external_volume=_volume(item.external_volume_kg, unit),
            )
            for item in value.series
        ),
    )


def _totals(value: OverviewTotals, unit: LoadUnit) -> OverviewTotalsResponse:
    return OverviewTotalsResponse(
        workout_count=value.workout_count,
        active_day_count=value.active_day_count,
        set_count=value.set_count,
        repetition_count=value.repetition_count,
        external_volume=_volume(value.external_volume_kg, unit),
    )


def overview_statistics_response(value: OverviewStatisticsResult) -> OverviewStatisticsResponse:
    unit = value.preferred_load_unit
    return OverviewStatisticsResponse(
        period=_window(value.window),
        bucket=value.bucket,
        current=_totals(value.current, unit),
        previous=_totals(value.previous, unit) if value.previous is not None else None,
        series=tuple(
            OverviewBucketResponse(
                period_start=item.period_start,
                workout_count=item.workout_count,
                set_count=item.set_count,
                repetition_count=item.repetition_count,
                external_volume=_volume(item.external_volume_kg, unit),
            )
            for item in value.series
        ),
        top_exercises=tuple(
            TopExerciseResponse(
                exercise_id=item.exercise_id,
                exercise_name=item.exercise_name,
                workout_count=item.workout_count,
                set_count=item.set_count,
            )
            for item in value.top_exercises
        ),
        recent_records=tuple(
            EstimatedOneRepMaxRecordResponse(
                exercise_id=item.exercise_id,
                exercise_name=item.exercise_name,
                workout_id=item.workout_id,
                performed_on=item.performed_on,
                estimated_one_rep_max=_mass(item.estimated_one_rep_max_kg, unit),
                previous_best=_mass(item.previous_best_kg, unit),
            )
            for item in value.recent_records
        ),
    )
