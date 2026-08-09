"""Web-first, two-phase Telegram account linking."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from app.application.ports import UnitOfWorkFactory
from app.domain.exceptions import (
    EmailNotVerifiedError,
    InvalidCredentialsError,
    TelegramAlreadyLinkedError,
    TelegramLinkInvalidError,
    TelegramLinkNotFoundError,
    UserAlreadyHasTelegramError,
)
from app.domain.models import ExternalIdentity, TelegramLinkRequest
from app.infrastructure.security import PasswordService, new_opaque_token, token_hash


@dataclass(frozen=True, slots=True)
class CreatedTelegramLink:
    request: TelegramLinkRequest
    token: str


@dataclass(frozen=True, slots=True)
class TelegramConnectionStatus:
    linked: bool
    identity: ExternalIdentity | None = None


class TelegramLinkingService:
    def __init__(
        self, uow_factory: UnitOfWorkFactory, password_service: PasswordService,
        *, ttl_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._passwords = password_service
        self._ttl = timedelta(seconds=ttl_seconds)

    async def create_request(self, user_id: UUID) -> CreatedTelegramLink:
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            account = await uow.web_accounts.get_by_user_id(user_id)
            if account is None or account.email_verified_at is None:
                raise EmailNotVerifiedError("Verify your email before linking Telegram")
            existing = await uow.external_identities.get_by_user_provider(user_id, "telegram")
            if existing is not None:
                raise UserAlreadyHasTelegramError("This account already has Telegram linked")
            await uow.telegram_link_requests.acquire_user_lock(user_id)
            await uow.telegram_link_requests.revoke_pending_for_user(user_id, now)
            raw = new_opaque_token()
            request = await uow.telegram_link_requests.create(
                request_id=uuid4(), user_id=user_id, token_hash=token_hash(raw),
                created_at=now, expires_at=now + self._ttl,
            )
            await uow.commit()
            return CreatedTelegramLink(request=request, token=raw)

    async def get_request(self, user_id: UUID, request_id: UUID) -> TelegramLinkRequest:
        async with self._uow_factory() as uow:
            request = await uow.telegram_link_requests.get_by_id_for_user(request_id, user_id)
            if request is None:
                raise TelegramLinkNotFoundError("Telegram link request not found")
            return request

    async def claim(
        self, *, raw_token: str, telegram_user_id: int,
        username: str | None, display_name: str | None,
    ) -> TelegramLinkRequest:
        now = datetime.now(timezone.utc)
        subject = str(telegram_user_id)
        async with self._uow_factory() as uow:
            request = await uow.telegram_link_requests.get_by_hash_for_update(token_hash(raw_token))
            if request is None or request.status == "cancelled" or request.expires_at <= now:
                raise TelegramLinkInvalidError("Invalid or expired Telegram link")
            if request.status == "completed":
                if request.candidate_telegram_user_id == subject:
                    return request
                raise TelegramLinkInvalidError("Telegram link was already completed")
            if request.status == "pending_web_confirmation":
                if request.candidate_telegram_user_id == subject:
                    return request
                raise TelegramLinkInvalidError("Telegram link already has a different candidate")
            request = await uow.telegram_link_requests.set_candidate(
                request.id, telegram_user_id=subject,
                username=username, display_name=display_name,
            )
            await uow.commit()
            return request

    async def confirm(self, user_id: UUID, request_id: UUID) -> ExternalIdentity:
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            request = await uow.telegram_link_requests.get_by_id_for_update(request_id, user_id)
            if (
                request is None or request.status != "pending_web_confirmation"
                or request.expires_at <= now or request.candidate_telegram_user_id is None
            ):
                raise TelegramLinkInvalidError("Telegram link is not ready for confirmation")
            subject = request.candidate_telegram_user_id
            await uow.external_identities.acquire_registration_lock("telegram", subject)
            by_subject = await uow.external_identities.get_by_provider_subject("telegram", subject)
            by_user = await uow.external_identities.get_by_user_provider(user_id, "telegram")
            if by_subject is not None and by_subject.user_id != user_id:
                raise TelegramAlreadyLinkedError("Telegram is linked to another account")
            if by_user is not None and by_user.provider_subject != subject:
                raise UserAlreadyHasTelegramError("This account already has another Telegram")
            if by_subject is not None:
                identity = await uow.external_identities.update_profile(
                    by_subject.id, username=request.candidate_username,
                    display_name=request.candidate_display_name,
                )
            else:
                identity = await uow.external_identities.create(
                    identity_id=uuid4(), user_id=user_id, provider="telegram",
                    provider_subject=subject, username=request.candidate_username,
                    display_name=request.candidate_display_name,
                )
            await uow.telegram_link_requests.complete(request.id, now)
            await uow.commit()
            return identity

    async def cancel(self, user_id: UUID, request_id: UUID) -> None:
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            request = await uow.telegram_link_requests.get_by_id_for_user(request_id, user_id)
            if request is None:
                raise TelegramLinkNotFoundError("Telegram link request not found")
            await uow.telegram_link_requests.cancel(request_id, now)
            await uow.commit()

    async def connection(self, user_id: UUID) -> TelegramConnectionStatus:
        async with self._uow_factory() as uow:
            identity = await uow.external_identities.get_by_user_provider(user_id, "telegram")
            return TelegramConnectionStatus(linked=identity is not None, identity=identity)

    async def resolve(
        self, *, telegram_user_id: int, username: str | None, display_name: str | None
    ) -> TelegramConnectionStatus:
        subject = str(telegram_user_id)
        async with self._uow_factory() as uow:
            identity = await uow.external_identities.get_by_provider_subject("telegram", subject)
            if identity is None:
                return TelegramConnectionStatus(linked=False)
            identity = await uow.external_identities.update_profile(
                identity.id, username=username, display_name=display_name
            )
            await uow.commit()
            return TelegramConnectionStatus(linked=True, identity=identity)

    async def unlink(self, user_id: UUID, password: str) -> None:
        now = datetime.now(timezone.utc)
        async with self._uow_factory() as uow:
            account = await uow.web_accounts.get_by_user_id(user_id)
            if account is None or not self._passwords.verify(password, account.password_hash):
                raise InvalidCredentialsError("Invalid password")
            identity = await uow.external_identities.get_by_user_provider(user_id, "telegram")
            if identity is not None:
                await uow.external_identities.delete(identity.id)
            await uow.telegram_link_requests.revoke_pending_for_user(user_id, now)
            await uow.commit()
