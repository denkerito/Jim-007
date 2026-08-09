"""Public and internal Telegram linking endpoints."""

from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.auth import require_internal_token
from app.api.dependencies import PasswordHasher, UowFactory
from app.api.rate_limit import Limiter, enforce
from app.api.web_security import WebAuth, WebAuthCsrf, require_safe_public_mutation
from app.api.idempotency import IdempotencyKey
from app.application.telegram_linking import TelegramLinkingService
from app.config import Settings, get_settings
from app.domain.exceptions import (
    EmailNotVerifiedError, InvalidCredentialsError, TelegramAlreadyLinkedError,
    TelegramLinkInvalidError, TelegramLinkNotFoundError, UserAlreadyHasTelegramError,
)
from app.domain.models import ExternalIdentity, TelegramLinkRequest


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TelegramCandidateResponse(_Model):
    username: str | None = None
    display_name: str | None = None


class TelegramLinkResponse(_Model):
    id: UUID
    status: Literal[
        "pending_telegram", "pending_web_confirmation", "completed", "expired", "cancelled"
    ]
    expires_at: datetime
    deep_link: str | None = None
    candidate: TelegramCandidateResponse | None = None


class TelegramConnectionResponse(_Model):
    linked: bool
    username: str | None = None
    display_name: str | None = None


class UnlinkRequest(_Model):
    password: str = Field(min_length=12, max_length=128)


class TelegramClaimRequest(_Model):
    token: str = Field(min_length=20, max_length=64)
    telegram_user_id: int = Field(gt=0)
    username: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class TelegramResolveRequest(_Model):
    telegram_user_id: int = Field(gt=0)
    username: str | None = Field(default=None, max_length=255)
    display_name: str | None = Field(default=None, max_length=255)


class TelegramInternalResponse(_Model):
    kind: Literal["linked", "unlinked", "candidate_recorded"]
    user_id: UUID | None = None


def linking_service(
    uow_factory: UowFactory, password_service: PasswordHasher,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TelegramLinkingService:
    return TelegramLinkingService(
        uow_factory, password_service, ttl_seconds=settings.telegram_link_ttl_seconds
    )


LinkingService = Annotated[TelegramLinkingService, Depends(linking_service)]


def _link_response(value: TelegramLinkRequest, *, deep_link: str | None = None) -> TelegramLinkResponse:
    effective_status = "expired" if (
        value.status in {"pending_telegram", "pending_web_confirmation"}
        and value.expires_at <= datetime.now(timezone.utc)
    ) else value.status
    candidate = None
    if value.candidate_telegram_user_id is not None:
        candidate = TelegramCandidateResponse(
            username=value.candidate_username, display_name=value.candidate_display_name
        )
    return TelegramLinkResponse(
        id=value.id, status=effective_status, expires_at=value.expires_at,
        deep_link=deep_link, candidate=candidate,
    )


def _connection(identity: ExternalIdentity | None) -> TelegramConnectionResponse:
    return TelegramConnectionResponse(
        linked=identity is not None,
        username=identity.username if identity else None,
        display_name=identity.display_name if identity else None,
    )


router = APIRouter(prefix="/api/me", tags=["telegram-linking"])
_write = [Depends(require_safe_public_mutation)]


@router.post(
    "/telegram-link-requests", response_model=TelegramLinkResponse,
    status_code=status.HTTP_201_CREATED, dependencies=_write,
)
async def create_link_request(
    context: WebAuthCsrf, service: LinkingService, limiter: Limiter,
    settings: Annotated[Settings, Depends(get_settings)],
) -> TelegramLinkResponse:
    enforce(limiter, f"telegram-link:{context.user_id}", limit=settings.telegram_link_rate_limit_per_minute, window_seconds=60)
    try:
        created = await service.create_request(context.user_id)
    except (EmailNotVerifiedError, UserAlreadyHasTelegramError) as error:
        code = "email_not_verified" if isinstance(error, EmailNotVerifiedError) else "user_already_has_telegram"
        http_status = status.HTTP_403_FORBIDDEN if isinstance(error, EmailNotVerifiedError) else status.HTTP_409_CONFLICT
        raise HTTPException(http_status, detail={"code": code, "message": str(error)}) from error
    payload = f"link_{created.token}"
    deep_link = f"https://t.me/{settings.telegram_bot_username}?start={payload}"
    return _link_response(created.request, deep_link=deep_link)


@router.get("/telegram-link-requests/{request_id}", response_model=TelegramLinkResponse)
async def get_link_request(
    request_id: UUID, context: WebAuth, service: LinkingService,
) -> TelegramLinkResponse:
    try:
        value = await service.get_request(context.user_id, request_id)
    except TelegramLinkNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "telegram_link_not_found", "message": str(error)}) from error
    return _link_response(value)


@router.post(
    "/telegram-link-requests/{request_id}/confirm",
    response_model=TelegramConnectionResponse, dependencies=_write,
)
async def confirm_link_request(
    request_id: UUID, context: WebAuthCsrf, service: LinkingService,
) -> TelegramConnectionResponse:
    try:
        identity = await service.confirm(context.user_id, request_id)
    except TelegramLinkInvalidError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "telegram_link_invalid", "message": str(error)}) from error
    except (TelegramAlreadyLinkedError, UserAlreadyHasTelegramError) as error:
        code = "telegram_already_linked" if isinstance(error, TelegramAlreadyLinkedError) else "user_already_has_telegram"
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": code, "message": str(error)}) from error
    return _connection(identity)


@router.delete(
    "/telegram-link-requests/{request_id}", status_code=status.HTTP_204_NO_CONTENT,
    dependencies=_write,
)
async def cancel_link_request(
    request_id: UUID, context: WebAuthCsrf, service: LinkingService,
) -> None:
    try:
        await service.cancel(context.user_id, request_id)
    except TelegramLinkNotFoundError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail={"code": "telegram_link_not_found", "message": str(error)}) from error


@router.get("/telegram-connection", response_model=TelegramConnectionResponse)
async def get_telegram_connection(
    context: WebAuth, service: LinkingService,
) -> TelegramConnectionResponse:
    result = await service.connection(context.user_id)
    return _connection(result.identity)


@router.post("/telegram-connection/unlink", status_code=status.HTTP_204_NO_CONTENT, dependencies=_write)
async def unlink_telegram(
    request: UnlinkRequest, context: WebAuthCsrf, service: LinkingService,
) -> None:
    try:
        await service.unlink(context.user_id, request.password)
    except InvalidCredentialsError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "invalid_credentials", "message": str(error)}) from error


internal_router = APIRouter(
    prefix="/internal", tags=["telegram-linking-internal"],
    dependencies=[Depends(require_internal_token)],
)


@internal_router.post("/telegram-link-requests/claim", response_model=TelegramInternalResponse)
async def claim_telegram_link(
    request: TelegramClaimRequest, service: LinkingService, idempotency_key: IdempotencyKey,
) -> TelegramInternalResponse:
    del idempotency_key
    try:
        await service.claim(
            raw_token=request.token, telegram_user_id=request.telegram_user_id,
            username=request.username, display_name=request.display_name,
        )
    except TelegramLinkInvalidError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail={"code": "telegram_link_invalid", "message": str(error)}) from error
    return TelegramInternalResponse(kind="candidate_recorded")


@internal_router.post("/telegram-connections/resolve", response_model=TelegramInternalResponse)
async def resolve_telegram_connection(
    request: TelegramResolveRequest, service: LinkingService,
) -> TelegramInternalResponse:
    result = await service.resolve(
        telegram_user_id=request.telegram_user_id,
        username=request.username, display_name=request.display_name,
    )
    return TelegramInternalResponse(
        kind="linked" if result.linked else "unlinked",
        user_id=result.identity.user_id if result.identity else None,
    )
