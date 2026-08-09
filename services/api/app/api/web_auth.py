"""Public email/password authentication API."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.api.dependencies import EmailSender, UowFactory
from app.api.rate_limit import Limiter, client_key, enforce
from app.api.web_security import (
    SESSION_COOKIE, AuthService, WebAuth, WebAuthCsrf, require_safe_public_mutation,
)
from app.config import Settings, get_settings
from app.domain.exceptions import EmailNotVerifiedError, InvalidAuthTokenError, InvalidCredentialsError
from app.infrastructure.security import csrf_token
from app.application.web_auth import normalize_email


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CredentialsRequest(_Model):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class EmailRequest(_Model):
    email: EmailStr


class TokenRequest(_Model):
    token: str = Field(min_length=20, max_length=256)


class ResetPasswordRequest(TokenRequest):
    new_password: str = Field(min_length=12, max_length=128)


class SessionResponse(_Model):
    user_id: str
    email: str
    email_verified: bool
    csrf_token: str
    telegram_linked: bool


router = APIRouter(prefix="/api/auth", tags=["web-auth"])
_public_write = [Depends(require_safe_public_mutation)]


def _set_session_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE, token, max_age=settings.session_ttl_seconds,
        httponly=True, secure=settings.session_cookie_secure,
        samesite="lax", path="/",
    )
    response.headers["Cache-Control"] = "no-store"


async def _send_token_email(
    sender: EmailSender, settings: Settings, *, recipient: str, token: str, purpose: str
) -> None:
    if purpose == "verify_email":
        url = f"{settings.public_web_url.rstrip('/')}/verify-email#token={token}"
        subject = "Verifica il tuo account JIM007"
        text = f"Apri questo link per verificare il tuo account JIM007:\n\n{url}\n"
    else:
        url = f"{settings.public_web_url.rstrip('/')}/reset-password#token={token}"
        subject = "Reimposta la password JIM007"
        text = f"Apri questo link per reimpostare la password JIM007:\n\n{url}\n"
    await sender.send(recipient=recipient, subject=subject, text=text)


@router.post("/register", status_code=status.HTTP_202_ACCEPTED, dependencies=_public_write)
async def register(
    request: CredentialsRequest, http_request: Request, service: AuthService, sender: EmailSender,
    limiter: Limiter,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    enforce(limiter, f"register:{client_key(http_request)}:{normalize_email(str(request.email))}", limit=settings.email_rate_limit_per_hour, window_seconds=3600)
    issued = await service.register(email=str(request.email), password=request.password)
    if issued is not None:
        await _send_token_email(sender, settings, recipient=issued.email, token=issued.token, purpose="verify_email")


@router.post("/resend-verification", status_code=status.HTTP_202_ACCEPTED, dependencies=_public_write)
async def resend_verification(
    request: EmailRequest, http_request: Request, service: AuthService, sender: EmailSender,
    limiter: Limiter,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    enforce(limiter, f"resend:{client_key(http_request)}:{normalize_email(str(request.email))}", limit=settings.email_rate_limit_per_hour, window_seconds=3600)
    issued = await service.issue_email_token(email=str(request.email), purpose="verify_email")
    if issued is not None:
        await _send_token_email(sender, settings, recipient=issued.email, token=issued.token, purpose="verify_email")


@router.post("/verify-email", status_code=status.HTTP_204_NO_CONTENT, dependencies=_public_write)
async def verify_email(
    request: TokenRequest, response: Response, service: AuthService,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        session = await service.verify_email(request.token)
    except InvalidAuthTokenError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_auth_token", "message": str(error)}) from error
    _set_session_cookie(response, session.token, settings)


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT, dependencies=_public_write)
async def login(
    request: CredentialsRequest, http_request: Request, response: Response, service: AuthService,
    limiter: Limiter,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    enforce(limiter, f"login:{client_key(http_request)}:{normalize_email(str(request.email))}", limit=settings.login_rate_limit_per_minute, window_seconds=60)
    try:
        session = await service.login(email=str(request.email), password=request.password)
    except InvalidCredentialsError as error:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "invalid_credentials", "message": str(error)}) from error
    except EmailNotVerifiedError as error:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "email_not_verified", "message": str(error)}) from error
    _set_session_cookie(response, session.token, settings)


@router.get("/session", response_model=SessionResponse)
async def get_session(
    context: WebAuth,
    uow_factory: UowFactory,
    settings: Annotated[Settings, Depends(get_settings)],
    response: Response,
) -> SessionResponse:
    response.headers["Cache-Control"] = "no-store"
    async with uow_factory() as uow:
        telegram = await uow.external_identities.get_by_user_provider(
            context.user_id, "telegram"
        )
    return SessionResponse(
        user_id=str(context.user_id), email=context.account.email,
        email_verified=context.account.email_verified_at is not None,
        csrf_token=csrf_token(context.session_token, settings.csrf_secret.get_secret_value()),
        telegram_linked=telegram is not None,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, dependencies=_public_write)
async def logout(
    context: WebAuthCsrf, response: Response, service: AuthService,
) -> None:
    await service.logout(context.session_token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.headers["Cache-Control"] = "no-store"


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED, dependencies=_public_write)
async def forgot_password(
    request: EmailRequest, http_request: Request, service: AuthService, sender: EmailSender,
    limiter: Limiter,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    enforce(limiter, f"forgot:{client_key(http_request)}:{normalize_email(str(request.email))}", limit=settings.email_rate_limit_per_hour, window_seconds=3600)
    issued = await service.issue_email_token(email=str(request.email), purpose="reset_password")
    if issued is not None:
        await _send_token_email(sender, settings, recipient=issued.email, token=issued.token, purpose="reset_password")


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT, dependencies=_public_write)
async def reset_password(
    request: ResetPasswordRequest, response: Response, service: AuthService,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    try:
        session = await service.reset_password(raw_token=request.token, new_password=request.new_password)
    except InvalidAuthTokenError as error:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail={"code": "invalid_auth_token", "message": str(error)}) from error
    _set_session_cookie(response, session.token, settings)
