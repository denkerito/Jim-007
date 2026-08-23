"""Session-authenticated statistics APIs for the web application."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import UowFactory
from app.api.statistics_schemas import (
    ExerciseStatisticsResponse,
    OverviewStatisticsResponse,
    exercise_statistics_response,
    overview_statistics_response,
)
from app.api.web_security import WebAuth
from app.application.statistics import (
    GetExerciseStatistics,
    GetOverviewStatistics,
    StatisticsPeriod,
)


router = APIRouter(prefix="/api/me", tags=["web-statistics"])


@router.get("/statistics/overview", response_model=OverviewStatisticsResponse)
async def get_my_statistics_overview(
    context: WebAuth,
    uow_factory: UowFactory,
    period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.FOUR_WEEKS,
) -> OverviewStatisticsResponse:
    result = await GetOverviewStatistics(uow_factory).execute(
        user_id=context.user_id, period=period
    )
    return overview_statistics_response(result)


@router.get(
    "/exercises/{exercise_id}/statistics", response_model=ExerciseStatisticsResponse
)
async def get_my_exercise_statistics(
    exercise_id: UUID,
    context: WebAuth,
    uow_factory: UowFactory,
    period: Annotated[StatisticsPeriod, Query()] = StatisticsPeriod.FOUR_WEEKS,
) -> ExerciseStatisticsResponse:
    result = await GetExerciseStatistics(uow_factory).execute(
        user_id=context.user_id,
        exercise_id=exercise_id,
        period=period,
    )
    return exercise_statistics_response(result)
