"""Internal endpoint for Telegram programmed-workout commands."""

from fastapi import APIRouter, Depends

from app.api.auth import require_internal_token
from app.api.dependencies import UowFactory, WorkoutInterpreter
from app.api.idempotency import IdempotencyKey, hash_canonical_json
from app.api.schemas import ProgramEventRequest, ProgramEventResponse, program_event_response
from app.application.commands import ProcessProgramEventCommand
from app.application.program_events import ProcessProgramEvent

router = APIRouter(
    prefix="/internal/program-events", tags=["program-events"],
    dependencies=[Depends(require_internal_token)],
)


@router.post("", response_model=ProgramEventResponse)
async def process_program_event(
    request: ProgramEventRequest, idempotency_key: IdempotencyKey,
    uow_factory: UowFactory, interpreter: WorkoutInterpreter,
) -> ProgramEventResponse:
    result = await ProcessProgramEvent(uow_factory, interpreter).execute(
        ProcessProgramEventCommand(
            **request.model_dump(), idempotency_key=idempotency_key,
            request_hash=hash_canonical_json(request.model_dump(mode="json", exclude_none=False)),
        )
    )
    return program_event_response(result)
