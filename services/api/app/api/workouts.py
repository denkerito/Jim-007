"""Internal HTTP endpoints for the incremental workout lifecycle."""

import hashlib
import json
from functools import partial
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.api.auth import require_internal_token
from app.api.schemas import (
    AddExerciseRequest,
    CreateWorkoutRequest,
    WorkoutExerciseResponse,
    WorkoutResponse,
    workout_exercise_response,
    workout_response,
)
from app.application.commands import (
    AddExerciseToWorkoutCommand,
    CompleteWorkoutCommand,
    CreateWorkoutCommand,
)
from app.application.services import AddExerciseToWorkout, CompleteWorkout, CreateWorkout
from app.application.ports import UnitOfWorkFactory
from app.infrastructure.database.session import session_factory
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork


router = APIRouter(
    prefix="/users/{user_id}/workouts",
    tags=["workouts"],
    dependencies=[Depends(require_internal_token)],
)


def get_uow_factory() -> UnitOfWorkFactory:
    return partial(SqlAlchemyUnitOfWork, session_factory)


def _request_hash(operation: str, path_values: dict[str, UUID], payload: object) -> str:
    if hasattr(payload, "model_dump"):
        body = payload.model_dump(mode="json", exclude_none=False)  # type: ignore[attr-defined]
    else:
        body = payload
    canonical = json.dumps(
        {"operation": operation, "path": {key: str(value) for key, value in path_values.items()}, "body": body},
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotency_key(
    value: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_idempotency_key", "message": "Idempotency-Key must not be blank"},
        )
    return value


IdempotencyKey = Annotated[str, Depends(_idempotency_key)]
UowFactory = Annotated[UnitOfWorkFactory, Depends(get_uow_factory)]


@router.post("", response_model=WorkoutResponse, status_code=status.HTTP_201_CREATED)
async def create_workout(
    user_id: UUID,
    request: CreateWorkoutRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    uow_factory: UowFactory,
) -> WorkoutResponse:
    result = await CreateWorkout(uow_factory).execute(
        CreateWorkoutCommand(
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=_request_hash("create_workout", {"user_id": user_id}, request),
            performed_on=request.performed_on,
            notes=request.notes,
        )
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return workout_response(result.value)


@router.post(
    "/{workout_id}/exercises",
    response_model=WorkoutExerciseResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_exercise(
    user_id: UUID,
    workout_id: UUID,
    request: AddExerciseRequest,
    response: Response,
    idempotency_key: IdempotencyKey,
    uow_factory: UowFactory,
) -> WorkoutExerciseResponse:
    result = await AddExerciseToWorkout(uow_factory).execute(
        AddExerciseToWorkoutCommand(
            user_id=user_id,
            workout_id=workout_id,
            idempotency_key=idempotency_key,
            request_hash=_request_hash(
                "add_workout_exercise",
                {"user_id": user_id, "workout_id": workout_id},
                request,
            ),
            exercise=request.exercise.model_dump(),
            sets=tuple(item.model_dump() for item in request.sets),
            notes=request.notes,
        )
    )
    if result.replayed:
        response.status_code = status.HTTP_200_OK
    return workout_exercise_response(result.value)


@router.post("/{workout_id}/complete", response_model=WorkoutResponse)
async def complete_workout(
    user_id: UUID,
    workout_id: UUID,
    idempotency_key: IdempotencyKey,
    uow_factory: UowFactory,
) -> WorkoutResponse:
    result = await CompleteWorkout(uow_factory).execute(
        CompleteWorkoutCommand(
            user_id=user_id,
            workout_id=workout_id,
            idempotency_key=idempotency_key,
            request_hash=_request_hash(
                "complete_workout",
                {"user_id": user_id, "workout_id": workout_id},
                {},
            ),
        )
    )
    return workout_response(result.value)
