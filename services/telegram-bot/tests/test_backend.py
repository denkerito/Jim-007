import json
from uuid import uuid4

import httpx
import pytest

from app.backend import BackendClient, BackendError


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(
        status_code,
        json={
            "user_id": str(uuid4()),
            "locale": "it-IT",
            "timezone": "Europe/Rome",
            "preferred_load_unit": "kg",
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("status_code", "created"), [(201, True), (200, False)])
async def test_registration_contract(status_code: int, created: bool) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/identities/telegram"
        assert request.headers["Authorization"] == "Bearer internal-secret"
        assert json.loads(request.content) == {
            "telegram_user_id": 12345,
            "username": "first_name",
            "display_name": "First User",
        }
        return _response(status_code)

    client = BackendClient(
        base_url="http://api:8000/",
        internal_api_token="internal-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.register_telegram_user(
            telegram_user_id=12345,
            username="first_name",
            display_name="First User",
        )
        assert result.created is created
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        lambda request: httpx.Response(503),
        lambda request: httpx.Response(200, json={"unexpected": "body"}),
    ],
)
async def test_registration_rejects_backend_and_protocol_errors(handler) -> None:
    client = BackendClient(
        base_url="http://api:8000",
        internal_api_token="internal-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(BackendError):
            await client.register_telegram_user(
                telegram_user_id=12345,
                username=None,
                display_name=None,
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_registration_wraps_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = BackendClient(
        base_url="http://api:8000",
        internal_api_token="internal-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(BackendError):
            await client.register_telegram_user(
                telegram_user_id=12345,
                username=None,
                display_name=None,
            )
    finally:
        await client.close()
