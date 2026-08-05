"""Internal endpoint for provider-neutral chat workout events."""

import hashlib
import json

from fastapi import APIRouter, Depends

from app.api.auth import require_internal_token
from app.api.dependencies import UowFactory, WorkoutInterpreter
from app.api.schemas import (
    WorkoutEventRequest,
    WorkoutEventResponse,
    workout_event_response,
)
from app.api.workouts import IdempotencyKey
from app.application.commands import ProcessWorkoutEventCommand
from app.application.workout_events import ProcessWorkoutEvent


router = APIRouter(
    prefix="/internal/workout-events",
    tags=["workout-events"],
    dependencies=[Depends(require_internal_token)],
)


def _request_hash(request: WorkoutEventRequest) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json", exclude_none=False),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@router.post("", response_model=WorkoutEventResponse)
async def process_workout_event(
    request: WorkoutEventRequest,
    idempotency_key: IdempotencyKey,
    uow_factory: UowFactory,
    interpreter: WorkoutInterpreter,
) -> WorkoutEventResponse:
    result = await ProcessWorkoutEvent(uow_factory, interpreter).execute(
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
