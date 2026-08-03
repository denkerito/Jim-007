"""HTTP client for the internal JIM007 API."""

import asyncio
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError


class BackendError(RuntimeError):
    """Raised when registration cannot be completed or validated."""


class _RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    user_id: UUID
    locale: str
    timezone: str
    preferred_load_unit: str


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    user_id: UUID
    created: bool


class BackendClient:
    def __init__(
        self,
        *,
        base_url: str,
        internal_api_token: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {internal_api_token}"},
            timeout=httpx.Timeout(5.0),
            transport=transport,
        )

    async def register_telegram_user(
        self,
        *,
        telegram_user_id: int,
        username: str | None,
        display_name: str | None,
    ) -> RegistrationResult:
        payload: dict[str, Any] = {
            "telegram_user_id": telegram_user_id,
            "username": username,
            "display_name": display_name,
        }
        try:
            async with asyncio.timeout(5.0):
                response = await self._client.post(
                    "/internal/identities/telegram", json=payload
                )
            response.raise_for_status()
            parsed = _RegistrationResponse.model_validate(response.json())
        except (httpx.HTTPError, TimeoutError, ValidationError, ValueError) as error:
            raise BackendError("Backend registration failed") from error

        if response.status_code not in (200, 201):
            raise BackendError("Backend returned an unexpected registration status")
        return RegistrationResult(
            user_id=parsed.user_id,
            created=response.status_code == 201,
        )

    async def close(self) -> None:
        await self._client.aclose()
