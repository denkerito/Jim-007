"""Internal endpoint for provider-neutral chat workout events."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.auth import require_internal_token
from app.api.dependencies import UowFactory, WorkoutInterpreter
from app.api.idempotency import IdempotencyKey, hash_canonical_json
from app.api.schemas import (
    INTERNAL_LLM_ERROR_RESPONSES,
    WorkoutEventRequest,
    WorkoutEventResponse,
    workout_event_response,
)
from app.application.commands import ProcessWorkoutEventCommand
from app.application.workout_events import ProcessWorkoutEvent
from app.config import Settings, get_settings


router = APIRouter(
    prefix="/internal/workout-events",
    tags=["workout-events"],
    dependencies=[Depends(require_internal_token)],
)


def _request_hash(request: WorkoutEventRequest) -> str:
    return hash_canonical_json(
        request.model_dump(mode="json", exclude_none=False)
    )


@router.post(
    "",
    response_model=WorkoutEventResponse,
    responses=INTERNAL_LLM_ERROR_RESPONSES,
)
async def process_workout_event(
    request: WorkoutEventRequest,
    idempotency_key: IdempotencyKey,
    uow_factory: UowFactory,
    interpreter: WorkoutInterpreter,
    settings: Annotated[Settings, Depends(get_settings)],
) -> WorkoutEventResponse:
    result = await ProcessWorkoutEvent(
        uow_factory,
        interpreter,
        clarification_ttl_seconds=settings.llm_clarification_ttl_seconds,
    ).execute(
        ProcessWorkoutEventCommand(
            provider=request.provider,
            provider_subject=request.provider_subject,
            action=request.action,
            text=request.text,
            idempotency_key=idempotency_key,
            request_hash=_request_hash(request),
        )
    )
    return workout_event_response(result)
