"""Session-authenticated workout history and exercise catalog APIs."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status

from app.api.dependencies import UowFactory
from app.api.schemas import (
    CreateExerciseRequest,
    CreateExerciseResponse,
    ExerciseHistoryPageResponse,
    ExerciseListResponse,
    ExerciseResponse,
    RenameExerciseRequest,
    WorkoutHistoryPageResponse,
    exercise_history_page_response,
    workout_history_page_response,
)
from app.api.web_security import WebAuth, WebAuthCsrf, require_safe_public_mutation
from app.application.exercises import CreateExercise, RenameExercise
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


@router.post(
    "/exercises",
    response_model=CreateExerciseResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        status.HTTP_200_OK: {
            "model": CreateExerciseResponse,
            "description": "The normalized exercise already existed.",
        }
    },
    dependencies=[Depends(require_safe_public_mutation)],
)
async def create_my_exercise(
    request: CreateExerciseRequest,
    response: Response,
    context: WebAuthCsrf,
    uow_factory: UowFactory,
) -> CreateExerciseResponse:
    result = await CreateExercise(uow_factory).execute(
        user_id=context.user_id,
        name=request.name,
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    exercise = result.exercise
    return CreateExerciseResponse(
        exercise=ExerciseResponse(
            id=exercise.id,
            name=exercise.name,
            normalized_name=exercise.normalized_name,
        ),
        created=result.created,
    )


@router.patch(
    "/exercises/{exercise_id}",
    response_model=ExerciseResponse,
    responses={
        status.HTTP_404_NOT_FOUND: {"description": "Exercise not found."},
        status.HTTP_409_CONFLICT: {
            "description": "Another exercise already uses the normalized name."
        },
    },
    dependencies=[Depends(require_safe_public_mutation)],
)
async def rename_my_exercise(
    exercise_id: UUID,
    request: RenameExerciseRequest,
    context: WebAuthCsrf,
    uow_factory: UowFactory,
) -> ExerciseResponse:
    exercise = await RenameExercise(uow_factory).execute(
        user_id=context.user_id,
        exercise_id=exercise_id,
        name=request.name,
    )
    return ExerciseResponse(
        id=exercise.id,
        name=exercise.name,
        normalized_name=exercise.normalized_name,
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
