from functools import partial
from datetime import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.workouts import get_uow_factory
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.main import app


@pytest.mark.asyncio
async def test_http_lifecycle_and_authentication(
    session_factory: async_sessionmaker[AsyncSession], user_id
) -> None:
    app.dependency_overrides[get_uow_factory] = lambda: partial(
        SqlAlchemyUnitOfWork, session_factory
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        unauthorized = await client.post(
            f"/users/{user_id}/workouts", headers={"Idempotency-Key": "create"}, json={}
        )
        assert unauthorized.status_code == 401

        headers = {
            "Authorization": "Bearer integration-secret",
            "Idempotency-Key": "create",
        }
        created = await client.post(f"/users/{user_id}/workouts", headers=headers, json={})
        assert created.status_code == 201
        workout_id = created.json()["id"]
        assert created.json()["performed_on"] == str(datetime.now(ZoneInfo("Europe/Rome")).date())
        replay = await client.post(f"/users/{user_id}/workouts", headers=headers, json={})
        assert replay.status_code == 200
        assert replay.json()["id"] == workout_id

        headers["Idempotency-Key"] = "add"
        added = await client.post(
            f"/users/{user_id}/workouts/{workout_id}/exercises",
            headers=headers,
            json={
                "exercise": {"kind": "new", "name": "Bench Press"},
                "sets": [{"repetitions": 8, "load_value": "80"}],
            },
        )
        assert added.status_code == 201, added.text
        assert added.json()["sets"][0]["load"]["unit"] == "kg"

        headers["Idempotency-Key"] = "complete"
        completed = await client.post(
            f"/users/{user_id}/workouts/{workout_id}/complete", headers=headers
        )
        assert completed.status_code == 200, completed.text
        assert completed.json()["status"] == "completed"
        assert len(completed.json()["exercises"]) == 1

        headers["Idempotency-Key"] = "after-complete"
        blocked = await client.post(
            f"/users/{user_id}/workouts/{workout_id}/exercises",
            headers=headers,
            json={
                "exercise": {"kind": "new", "name": "Lat Machine"},
                "sets": [{"repetitions": 10}],
            },
        )
        assert blocked.status_code == 409

        headers["Idempotency-Key"] = "invalid"
        invalid = await client.post(
            f"/users/{user_id}/workouts/{workout_id}/exercises",
            headers=headers,
            json={
                "exercise": {"kind": "new", "name": "   "},
                "sets": [{"repetitions": 10}],
            },
        )
        assert invalid.status_code == 422

        headers["Idempotency-Key"] = "missing"
        missing = await client.post(
            f"/users/{user_id}/workouts/{uuid4()}/complete", headers=headers
        )
        assert missing.status_code == 404

        headers["Idempotency-Key"] = "empty-draft"
        empty_draft = await client.post(
            f"/users/{user_id}/workouts", headers=headers, json={}
        )
        assert empty_draft.status_code == 201
        headers["Idempotency-Key"] = "empty-complete"
        empty_complete = await client.post(
            f"/users/{user_id}/workouts/{empty_draft.json()['id']}/complete",
            headers=headers,
        )
        assert empty_complete.status_code == 409
    app.dependency_overrides.clear()
