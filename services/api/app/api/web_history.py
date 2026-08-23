"""Session-authenticated read APIs for the web application."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.dependencies import UowFactory
from app.api.schemas import (
    ExerciseHistoryPageResponse,
    ExerciseListResponse,
    ExerciseResponse,
    WorkoutHistoryPageResponse,
    exercise_history_page_response,
    workout_history_page_response,
)
from app.api.web_security import WebAuth
from app.application.history import (
    ListExerciseCatalog,
    ListExerciseHistory,
    ListWorkoutHistory,
)


router = APIRouter(prefix="/api/me", tags=["web-history"])


@router.get("/workouts", response_model=WorkoutHistoryPageResponse)
async def list_my_workouts(
    context: WebAuth,
    uow_factory: UowFactory,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> WorkoutHistoryPageResponse:
    page = await ListWorkoutHistory(uow_factory).execute(
        user_id=context.user_id, limit=limit, cursor=cursor
    )
    return workout_history_page_response(page)


@router.get("/exercises", response_model=ExerciseListResponse)
async def list_my_exercises(
    context: WebAuth,
    uow_factory: UowFactory,
) -> ExerciseListResponse:
    exercises = await ListExerciseCatalog(uow_factory).execute(user_id=context.user_id)
    return ExerciseListResponse(
        items=tuple(
            ExerciseResponse(
                id=item.id, name=item.name, normalized_name=item.normalized_name
            )
            for item in exercises
        )
    )


@router.get(
    "/exercises/{exercise_id}/history",
    response_model=ExerciseHistoryPageResponse,
)
async def list_my_exercise_history(
    exercise_id: UUID,
    context: WebAuth,
    uow_factory: UowFactory,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> ExerciseHistoryPageResponse:
    page = await ListExerciseHistory(uow_factory).execute(
        user_id=context.user_id,
        exercise_id=exercise_id,
        limit=limit,
        cursor=cursor,
    )
    return exercise_history_page_response(page)
