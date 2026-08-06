"""Read-only exercise history endpoints."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.auth import require_internal_token
from app.api.dependencies import UowFactory
from app.api.schemas import (
    ExerciseHistoryPageResponse,
    exercise_history_page_response,
)
from app.application.history import ListExerciseHistory

router = APIRouter(
    prefix="/users/{user_id}/exercises",
    tags=["exercises"],
    dependencies=[Depends(require_internal_token)],
)


@router.get("/{exercise_id}/history", response_model=ExerciseHistoryPageResponse)
async def list_exercise_history(
    user_id: UUID,
    exercise_id: UUID,
    uow_factory: UowFactory,
    limit: Annotated[int, Query(ge=1, le=20)] = 5,
    cursor: Annotated[str | None, Query(min_length=1, max_length=1024)] = None,
) -> ExerciseHistoryPageResponse:
    page = await ListExerciseHistory(uow_factory).execute(
        user_id=user_id,
        exercise_id=exercise_id,
        limit=limit,
        cursor=cursor,
    )
    return exercise_history_page_response(page)
