"""Internal query endpoint for the active chat workout."""

from fastapi import APIRouter, Depends

from app.api.auth import require_internal_token
from app.api.dependencies import UowFactory
from app.api.schemas import (
    INTERNAL_APPLICATION_ERROR_RESPONSES,
    WorkoutStatusRequest,
    WorkoutStatusResponse,
    workout_status_response,
)
from app.application.commands import GetWorkoutStatusCommand
from app.application.workout_status import GetWorkoutStatus


router = APIRouter(
    prefix="/internal/workout-status",
    tags=["workout-status"],
    dependencies=[Depends(require_internal_token)],
)


@router.post(
    "",
    response_model=WorkoutStatusResponse,
    responses=INTERNAL_APPLICATION_ERROR_RESPONSES,
)
async def get_workout_status(
    request: WorkoutStatusRequest,
    uow_factory: UowFactory,
) -> WorkoutStatusResponse:
    result = await GetWorkoutStatus(uow_factory).execute(
        GetWorkoutStatusCommand(
            provider=request.provider,
            provider_subject=request.provider_subject,
        )
    )
    return workout_status_response(result)
