"""Web registration, credentials, verification, reset, and sessions."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.application.ports import UnitOfWorkFactory
from app.domain.exceptions import (
    EmailNotVerifiedError,
    InvalidAuthTokenError,
    InvalidCredentialsError,
)
from app.domain.models import LoadUnit, WebAccount
from app.infrastructure.security import PasswordService, new_opaque_token, token_hash


def normalize_email(email: str) -> str:
    return email.strip().casefold()


@dataclass(frozen=True, slots=True)
class IssuedAuthToken:
    email: str
    token: str


@dataclass(frozen=True, slots=True)
class AuthenticatedSession:
    account: WebAccount
    token: str
    expires_at: datetime


class WebAuthService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, password_service: PasswordService,
        *, session_ttl_seconds: int, verification_ttl_seconds: int,
        reset_ttl_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._passwords = password_service
        self._session_ttl = timedelta(seconds=session_ttl_seconds)
        self._verification_ttl = timedelta(seconds=verification_ttl_seconds)
        self._reset_ttl = timedelta(seconds=reset_ttl_seconds)

    async def register(self, *, email: str, password: str) -> IssuedAuthToken | None:
        normalized = normalize_email(email)
        now = datetime.now(timezone.utc)
        password_hash = self._passwords.hash(password)
        async with self._uow_factory() as uow:
            await uow.web_accounts.acquire_email_lock(normalized)
            existing = await uow.web_accounts.get_by_normalized_email(normalized)
            if existing is not None:
                return None
            user = await uow.users.create(
                user_id=uuid4(), locale="it-IT", timezone="Europe/Rome",
                preferred_load_unit=LoadUnit.KG,
            )
            account = await uow.web_accounts.create(
                user_id=user.id, email=email.strip(), normalized_email=normalized,
                password_hash=password_hash,
            )
            raw = new_opaque_token()
            await uow.auth_tokens.create(
                token_id=uuid4(), user_id=account.user_id, purpose="verify_email",
                token_hash=token_hash(raw), created_at=now,
                expires_at=now + self._verification_ttl,
            )
            await uow.commit()
            return IssuedAuthToken(email=account.email, token=raw)

    async def issue_email_token(
        self, *, email: str, purpose: str
    ) -> IssuedAuthToken | None:
        normalized = normalize_email(email)
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            account = await uow.web_accounts.get_by_normalized_email(normalized)
            if account is None:
                return None
            if purpose == "verify_email" and account.email_verified_at is not None:
                return None
            await uow.auth_tokens.revoke_active(account.user_id, purpose, now)
            raw = new_opaque_token()
            ttl = self._verification_ttl if purpose == "verify_email" else self._reset_ttl
            await uow.auth_tokens.create(
                token_id=uuid4(), user_id=account.user_id, purpose=purpose,
                token_hash=token_hash(raw), created_at=now, expires_at=now + ttl,
            )
            await uow.commit()
            return IssuedAuthToken(email=account.email, token=raw)

    async def verify_email(self, raw_token: str) -> AuthenticatedSession:
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            token = await uow.auth_tokens.get_for_update(token_hash(raw_token))
            if (
                token is None or token.purpose != "verify_email"
                or token.consumed_at is not None or token.revoked_at is not None
                or token.expires_at <= now
            ):
                raise InvalidAuthTokenError("Invalid or expired verification token")
            account = await uow.web_accounts.verify_email(token.user_id, now)
            await uow.auth_tokens.consume(token.id, now)
            session = await self._create_session(uow, account, now)
            await uow.commit()
            return session

    async def login(self, *, email: str, password: str) -> AuthenticatedSession:
        normalized = normalize_email(email)
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            account = await uow.web_accounts.get_by_normalized_email(normalized)
            if account is None:
                self._passwords.verify_dummy(password)
                raise InvalidCredentialsError("Invalid email or password")
            if account.locked_until is not None and account.locked_until > now:
                raise InvalidCredentialsError("Invalid email or password")
            valid, updated_hash = self._passwords.verify_and_rehash(password, account.password_hash)
            if not valid:
                failures = account.failed_login_count + 1
                locked_until = now + timedelta(minutes=15) if failures >= 5 else None
                await uow.web_accounts.record_login_failure(
                    account.user_id, failed_count=failures, locked_until=locked_until
                )
                await uow.commit()
                raise InvalidCredentialsError("Invalid email or password")
            if account.email_verified_at is None:
                raise EmailNotVerifiedError("Verify your email before signing in")
            if updated_hash is not None:
                account = await uow.web_accounts.update_password(account.user_id, updated_hash)
            await uow.web_accounts.clear_login_failures(account.user_id)
            session = await self._create_session(uow, account, now)
            await uow.commit()
            return session

    async def reset_password(
        self, *, raw_token: str, new_password: str
    ) -> AuthenticatedSession:
        now = datetime.now(timezone.utc)
        password_hash = self._passwords.hash(new_password)
        async with self._uow_factory() as uow:
            token = await uow.auth_tokens.get_for_update(token_hash(raw_token))
            if (
                token is None or token.purpose != "reset_password"
                or token.consumed_at is not None or token.revoked_at is not None
                or token.expires_at <= now
            ):
                raise InvalidAuthTokenError("Invalid or expired reset token")
            account = await uow.web_accounts.update_password(token.user_id, password_hash)
            await uow.auth_tokens.consume(token.id, now)
            await uow.web_sessions.revoke_all_for_user(account.user_id, now)
            session = await self._create_session(uow, account, now)
            await uow.commit()
            return session

    async def resolve_session(self, raw_token: str) -> WebAccount | None:
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            session = await uow.web_sessions.get_active_by_hash(token_hash(raw_token), now)
            if session is None:
                return None
            return await uow.web_accounts.get_by_user_id(session.user_id)

    async def logout(self, raw_token: str) -> None:
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            await uow.web_sessions.revoke_by_hash(token_hash(raw_token), now)
            await uow.commit()

    async def _create_session(self, uow, account: WebAccount, now: datetime) -> AuthenticatedSession:
        raw = new_opaque_token()
        expires_at = now + self._session_ttl
        await uow.web_sessions.create(
            session_id=uuid4(), user_id=account.user_id, token_hash=token_hash(raw),
            created_at=now, expires_at=expires_at,
        )
        return AuthenticatedSession(account=account, token=raw, expires_at=expires_at)
