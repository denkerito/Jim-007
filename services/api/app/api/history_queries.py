"""Provider-neutral history queries received from chat adapters."""

from fastapi import APIRouter, Depends

from app.api.auth import require_internal_token
from app.api.dependencies import ExerciseHistoryInterpreter, UowFactory
from app.api.schemas import (
    HistoryQueryRequest,
    HistoryQueryResponse,
    history_query_response,
)
from app.application.commands import ProcessHistoryQueryCommand
from app.application.history import ProcessHistoryQuery

router = APIRouter(
    prefix="/internal/history-queries",
    tags=["history-queries"],
    dependencies=[Depends(require_internal_token)],
)


@router.post("", response_model=HistoryQueryResponse)
async def process_history_query(
    request: HistoryQueryRequest,
    uow_factory: UowFactory,
    interpreter: ExerciseHistoryInterpreter,
) -> HistoryQueryResponse:
    result = await ProcessHistoryQuery(uow_factory, interpreter).execute(
        ProcessHistoryQueryCommand(**request.model_dump())
    )
    return history_query_response(result)
