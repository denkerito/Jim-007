"""Internal endpoints for resolving external identities."""

from fastapi import APIRouter, Depends, Response, status

from app.api.auth import require_internal_token
from app.api.dependencies import UowFactory
from app.api.schemas import (
    INTERNAL_AUTH_ERROR_RESPONSES,
    TelegramRegistrationRequest,
    UserRegistrationResponse,
)
from app.application.commands import RegisterExternalIdentityCommand
from app.application.services import RegisterExternalIdentity


router = APIRouter(
    prefix="/internal/identities",
    tags=["identities"],
    dependencies=[Depends(require_internal_token)],
)


@router.post(
    "/telegram",
    response_model=UserRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        **INTERNAL_AUTH_ERROR_RESPONSES,
        status.HTTP_200_OK: {
            "model": UserRegistrationResponse,
            "description": "The Telegram identity was already registered.",
        }
    },
)
async def register_telegram_identity(
    request: TelegramRegistrationRequest,
    response: Response,
    uow_factory: UowFactory,
) -> UserRegistrationResponse:
    result = await RegisterExternalIdentity(uow_factory).execute(
        RegisterExternalIdentityCommand(
            provider="telegram",
            provider_subject=str(request.telegram_user_id),
            username=request.username,
            display_name=request.display_name,
        )
    )
    if not result.created:
        response.status_code = status.HTTP_200_OK
    return UserRegistrationResponse(
        user_id=result.user.id,
        locale=result.user.locale,
        timezone=result.user.timezone,
        preferred_load_unit=result.user.preferred_load_unit,
    )
