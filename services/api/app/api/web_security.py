"""Browser-session, origin, JSON, and CSRF dependencies."""

from dataclasses import dataclass
import hmac
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from app.api.dependencies import PasswordHasher, UowFactory
from app.application.web_auth import WebAuthService
from app.config import Settings, get_settings
from app.domain.models import WebAccount
from app.infrastructure.security import csrf_token


SESSION_COOKIE = "jim007_session"


@dataclass(frozen=True, slots=True)
class WebAuthContext:
    user_id: UUID
    account: WebAccount
    session_token: str


def auth_service(
    uow_factory: UowFactory, password_service: PasswordHasher,
    settings: Annotated[Settings, Depends(get_settings)],
) -> WebAuthService:
    return WebAuthService(
        uow_factory, password_service,
        session_ttl_seconds=settings.session_ttl_seconds,
        verification_ttl_seconds=settings.email_verification_ttl_seconds,
        reset_ttl_seconds=settings.password_reset_ttl_seconds,
    )


AuthService = Annotated[WebAuthService, Depends(auth_service)]


async def require_web_auth(
    service: AuthService,
    session_token: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> WebAuthContext:
    if not session_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "authentication_required", "message": "Authentication required"})
    account = await service.resolve_session(session_token)
    if account is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail={"code": "authentication_required", "message": "Authentication required"})
    return WebAuthContext(user_id=account.user_id, account=account, session_token=session_token)


WebAuth = Annotated[WebAuthContext, Depends(require_web_auth)]


async def require_safe_public_mutation(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail={"code": "json_required", "message": "JSON required"})
    origin = request.headers.get("origin")
    expected = urlsplit(settings.public_web_url)
    supplied = urlsplit(origin or "")
    if not origin or (supplied.scheme, supplied.netloc) != (
        expected.scheme,
        expected.netloc,
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "invalid_origin", "message": "Invalid origin"},
        )


async def require_web_auth_csrf(
    context: WebAuth,
    settings: Annotated[Settings, Depends(get_settings)],
    supplied: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> WebAuthContext:
    expected = csrf_token(context.session_token, settings.csrf_secret.get_secret_value())
    if supplied is None or not hmac.compare_digest(supplied, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": "invalid_csrf_token", "message": "Invalid CSRF token"})
    return context


WebAuthCsrf = Annotated[WebAuthContext, Depends(require_web_auth_csrf)]
