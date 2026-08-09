import asyncio
from urllib.parse import parse_qs, urlsplit

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.api.dependencies import get_email_sender, get_uow_factory
from app.api.web_security import SESSION_COOKIE
from app.infrastructure.database.models import (
    AppUser,
    AuthToken as AuthTokenRecord,
    TelegramLinkRequest as TelegramLinkRequestRecord,
    WebAccount as WebAccountRecord,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.main import app


class FakeEmailSender:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def send(self, *, recipient: str, subject: str, text: str) -> None:
        self.messages.append(text)


def _email_token(message: str) -> str:
    marker = "#token="
    return message.split(marker, 1)[1].split()[0]


@pytest.mark.asyncio
async def test_web_first_registration_and_two_phase_telegram_link(
    session_factory,
) -> None:
    email = FakeEmailSender()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(session_factory)
    app.dependency_overrides[get_email_sender] = lambda: email
    transport = ASGITransport(app=app)
    password = "a-secure-password"
    internal = {"Authorization": "Bearer integration-secret"}
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            registered = await client.post(
                "/api/auth/register", json={"email": "User@Example.com", "password": password}
            )
            assert registered.status_code == 202
            assert len(email.messages) == 1
            verification_token = _email_token(email.messages[0])
            async with session_factory() as database:
                stored = await database.scalar(select(AuthTokenRecord))
                assert stored is not None
                assert stored.token_hash != verification_token
                assert verification_token not in stored.token_hash

            verified = await client.post(
                "/api/auth/verify-email", json={"token": verification_token}
            )
            assert verified.status_code == 204
            cookie = verified.headers["set-cookie"].lower()
            assert "httponly" in cookie
            assert "samesite=lax" in cookie
            assert "path=/" in cookie
            assert "secure" not in cookie

            session = await client.get("/api/auth/session")
            assert session.status_code == 200
            csrf = session.json()["csrf_token"]
            headers = {"X-CSRF-Token": csrf}

            missing_csrf = await client.post(
                "/api/me/telegram-link-requests", json={}
            )
            assert missing_csrf.status_code == 403
            assert missing_csrf.json()["detail"]["code"] == "invalid_csrf_token"

            created = await client.post(
                "/api/me/telegram-link-requests", json={}, headers=headers
            )
            assert created.status_code == 201
            body = created.json()
            payload = parse_qs(urlsplit(body["deep_link"]).query)["start"][0]
            assert payload.startswith("link_")
            raw_link_token = payload.removeprefix("link_")
            async with session_factory() as database:
                stored_link = await database.scalar(select(TelegramLinkRequestRecord))
                assert stored_link is not None
                assert stored_link.token_hash != raw_link_token
                assert raw_link_token not in stored_link.token_hash

            claim_payload = {
                "token": raw_link_token,
                "telegram_user_id": 12345,
                "username": "gym_user",
                "display_name": "Gym User",
            }
            claimed, replay = await asyncio.gather(
                client.post(
                    "/internal/telegram-link-requests/claim",
                    headers={**internal, "Idempotency-Key": "telegram:update:1"},
                    json=claim_payload,
                ),
                client.post(
                    "/internal/telegram-link-requests/claim",
                    headers={**internal, "Idempotency-Key": "telegram:update:1"},
                    json=claim_payload,
                ),
            )
            assert claimed.status_code == 200
            assert claimed.json()["kind"] == "candidate_recorded"
            assert replay.status_code == 200

            conflicting_candidate = await client.post(
                "/internal/telegram-link-requests/claim",
                headers={**internal, "Idempotency-Key": "telegram:update:2"},
                json={"token": raw_link_token, "telegram_user_id": 67890},
            )
            assert conflicting_candidate.status_code == 409
            assert (
                conflicting_candidate.json()["detail"]["code"]
                == "telegram_link_invalid"
            )

            pending = await client.get(f"/api/me/telegram-link-requests/{body['id']}")
            assert pending.json()["status"] == "pending_web_confirmation"
            assert pending.json()["candidate"]["username"] == "gym_user"

            confirmed = await client.post(
                f"/api/me/telegram-link-requests/{body['id']}/confirm",
                json={}, headers=headers,
            )
            assert confirmed.status_code == 200
            assert confirmed.json()["linked"] is True

            second_link = await client.post(
                "/api/me/telegram-link-requests", json={}, headers=headers
            )
            assert second_link.status_code == 409
            assert (
                second_link.json()["detail"]["code"]
                == "user_already_has_telegram"
            )

            resolved = await client.post(
                "/internal/telegram-connections/resolve", headers=internal,
                json={"telegram_user_id": 12345, "username": "gym_user"},
            )
            assert resolved.status_code == 200
            assert resolved.json()["kind"] == "linked"

            async with AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"Origin": "http://localhost:3000"},
            ) as challenger:
                await challenger.post(
                    "/api/auth/register",
                    json={
                        "email": "challenger@example.com",
                        "password": "challenger-password",
                    },
                )
                await challenger.post(
                    "/api/auth/verify-email",
                    json={"token": _email_token(email.messages[-1])},
                )
                challenger_session = await challenger.get("/api/auth/session")
                challenger_headers = {
                    "X-CSRF-Token": challenger_session.json()["csrf_token"]
                }
                challenger_link = await challenger.post(
                    "/api/me/telegram-link-requests",
                    json={},
                    headers=challenger_headers,
                )
                challenger_payload = parse_qs(
                    urlsplit(challenger_link.json()["deep_link"]).query
                )["start"][0]
                await challenger.post(
                    "/internal/telegram-link-requests/claim",
                    headers={**internal, "Idempotency-Key": "telegram:update:3"},
                    json={
                        "token": challenger_payload.removeprefix("link_"),
                        "telegram_user_id": 12345,
                    },
                )
                collision = await challenger.post(
                    f"/api/me/telegram-link-requests/{challenger_link.json()['id']}/confirm",
                    json={},
                    headers=challenger_headers,
                )
                assert collision.status_code == 409
                assert (
                    collision.json()["detail"]["code"]
                    == "telegram_already_linked"
                )

            unlinked = await client.post(
                "/api/me/telegram-connection/unlink",
                json={"password": password}, headers=headers,
            )
            assert unlinked.status_code == 204
            resolved = await client.post(
                "/internal/telegram-connections/resolve", headers=internal,
                json={"telegram_user_id": 12345},
            )
            assert resolved.json()["kind"] == "unlinked"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_plain_telegram_resolve_never_creates_user(session_factory) -> None:
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(session_factory)
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/internal/telegram-connections/resolve",
                headers={"Authorization": "Bearer integration-secret"},
                json={"telegram_user_id": 99999},
            )
            assert response.status_code == 200
            assert response.json() == {"kind": "unlinked", "user_id": None}
        async with session_factory() as database:
            assert await database.scalar(select(func.count()).select_from(AppUser)) == 0
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_auth_mutation_rejects_missing_origin() -> None:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/auth/register",
            json={"email": "user@example.com", "password": "a-secure-password"},
        )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "invalid_origin"


@pytest.mark.asyncio
async def test_concurrent_duplicate_registration_creates_one_account(
    session_factory,
) -> None:
    email = FakeEmailSender()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_email_sender] = lambda: email
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            payload = {
                "email": "Concurrent@Example.com",
                "password": "a-secure-password",
            }
            first, second = await asyncio.gather(
                client.post("/api/auth/register", json=payload),
                client.post("/api/auth/register", json=payload),
            )
        assert first.status_code == second.status_code == 202
        assert len(email.messages) == 1
        async with session_factory() as database:
            assert (
                await database.scalar(
                    select(func.count()).select_from(WebAccountRecord)
                )
                == 1
            )
            assert await database.scalar(select(func.count()).select_from(AppUser)) == 1
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_password_reset_revokes_the_previous_session(session_factory) -> None:
    email = FakeEmailSender()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_email_sender] = lambda: email
    transport = ASGITransport(app=app)
    origin = {"Origin": "http://localhost:3000"}
    try:
        async with AsyncClient(
            transport=transport, base_url="http://test", headers=origin
        ) as client:
            await client.post(
                "/api/auth/register",
                json={
                    "email": "reset@example.com",
                    "password": "original-password",
                },
            )
            await client.post(
                "/api/auth/verify-email",
                json={"token": _email_token(email.messages[0])},
            )
            previous_session = client.cookies[SESSION_COOKIE]
            forgot = await client.post(
                "/api/auth/forgot-password", json={"email": "reset@example.com"}
            )
            assert forgot.status_code == 202
            reset = await client.post(
                "/api/auth/reset-password",
                json={
                    "token": _email_token(email.messages[1]),
                    "new_password": "replacement-password",
                },
            )
            assert reset.status_code == 204
            assert (await client.get("/api/auth/session")).status_code == 200

        async with AsyncClient(
            transport=transport, base_url="http://test", headers=origin
        ) as old_client:
            old_client.cookies.set(SESSION_COOKIE, previous_session)
            assert (await old_client.get("/api/auth/session")).status_code == 401
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_concurrent_link_creation_leaves_one_pending_request(
    session_factory,
) -> None:
    email = FakeEmailSender()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_email_sender] = lambda: email
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"Origin": "http://localhost:3000"},
        ) as client:
            await client.post(
                "/api/auth/register",
                json={
                    "email": "parallel-link@example.com",
                    "password": "a-secure-password",
                },
            )
            await client.post(
                "/api/auth/verify-email",
                json={"token": _email_token(email.messages[0])},
            )
            session = await client.get("/api/auth/session")
            headers = {"X-CSRF-Token": session.json()["csrf_token"]}
            first, second = await asyncio.gather(
                client.post(
                    "/api/me/telegram-link-requests", json={}, headers=headers
                ),
                client.post(
                    "/api/me/telegram-link-requests", json={}, headers=headers
                ),
            )
        assert first.status_code == second.status_code == 201
        async with session_factory() as database:
            pending = await database.scalar(
                select(func.count())
                .select_from(TelegramLinkRequestRecord)
                .where(
                    TelegramLinkRequestRecord.status.in_(
                        ("pending_telegram", "pending_web_confirmation")
                    )
                )
            )
            cancelled = await database.scalar(
                select(func.count())
                .select_from(TelegramLinkRequestRecord)
                .where(TelegramLinkRequestRecord.status == "cancelled")
            )
        assert pending == 1
        assert cancelled == 1
    finally:
        app.dependency_overrides.clear()
