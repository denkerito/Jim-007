from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from app.backend import BackendClient, BackendError


pytestmark = pytest.mark.contract

MANIFEST_PATH = Path(__file__).parents[3] / "contracts" / "internal-api" / "v1" / "interactions.json"


def _manifest() -> dict[str, Any]:
    raw = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    fixtures = raw["fixtures"]

    def resolve(value: object) -> object:
        if isinstance(value, dict):
            if set(value) == {"$fixture"}:
                return resolve(copy.deepcopy(fixtures[value["$fixture"]]))
            return {key: resolve(child) for key, child in value.items()}
        if isinstance(value, list):
            return [resolve(child) for child in value]
        return value

    raw["interactions"] = resolve(raw["interactions"])
    return raw


async def _invoke(client: BackendClient, interaction: dict[str, Any]):
    operation = interaction["operation"]
    arguments = interaction["arguments"]
    if operation == "registration":
        return await client.register_telegram_user(**arguments)
    if operation == "workout_event":
        return await client.process_workout_event(**arguments)
    if operation == "program_event":
        return await client.process_program_event(**arguments)
    if operation == "status":
        return await client.get_workout_status(**arguments)
    if operation == "history":
        return await client.query_history(**arguments)
    raise AssertionError(f"Unsupported contract operation: {operation}")


@pytest.mark.asyncio
@pytest.mark.parametrize("interaction", _manifest()["interactions"], ids=lambda item: item["id"])
async def test_backend_client_honors_contract(interaction: dict[str, Any]) -> None:
    expected_request = interaction["request"]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == expected_request["path"]
        assert request.headers["Authorization"] == "Bearer contract-secret"
        assert json.loads(request.content) == expected_request["body"]
        expected_key = expected_request["idempotency_key"]
        if expected_key is None:
            assert "Idempotency-Key" not in request.headers
        else:
            assert request.headers["Idempotency-Key"] == expected_key
        return httpx.Response(interaction["status"], json=interaction["response"])

    client = BackendClient(
        base_url="http://contract",
        internal_api_token="contract-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        if "expected_error_code" in interaction:
            with pytest.raises(BackendError) as captured:
                await _invoke(client, interaction)
            assert captured.value.code == interaction["expected_error_code"]
            return
        result = await _invoke(client, interaction)
    finally:
        await client.close()

    expected_kind = interaction["expected_kind"]
    if interaction["operation"] == "registration":
        assert result.created is (expected_kind == "created")
    else:
        assert result.kind == expected_kind

    if interaction["id"] == "workout-logged":
        assert result.exercises[0].name == "Bench Press"
        assert result.exercises[0].notes == "Pausa al petto"
        assert str(result.exercises[0].sets[0].load_value) == "80.000"
    elif interaction["id"] == "workout-opened":
        assert result.program_workout.alias == "push"
        assert result.program_history[0].planned.rest_seconds == 120
        assert result.program_history[0].occurrences[0].sets[0].notes == "RPE 8"
    elif interaction["id"] == "workout-undone":
        assert result.removed_exercises[0].name == "Bench Press"
    elif interaction["id"] in {"program-created", "program-edited"}:
        assert result.program_workout.items[0].rest_seconds == 120
    elif interaction["id"] == "status-active":
        assert result.workout.program_workout.alias == "push"
        assert result.workout.exercises[0].sets[0].notes == "RPE 8"
    elif interaction["id"] == "history-workouts":
        assert result.workouts[0].program_workout.alias == "push"
    elif interaction["id"] == "history-exercise":
        assert result.exercise_workouts[0].occurrences[0].notes == "Pausa al petto"


@pytest.mark.asyncio
async def test_backend_client_tolerates_additive_response_fields() -> None:
    response = {"kind": "none", "future_field": {"nested": True}}

    client = BackendClient(
        base_url="http://contract",
        internal_api_token="contract-secret",
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=response)),
    )
    try:
        result = await client.get_workout_status(telegram_user_id=12345)
    finally:
        await client.close()
    assert result.kind == "none"


@pytest.mark.asyncio
async def test_backend_client_rejects_unknown_discriminator() -> None:
    client = BackendClient(
        base_url="http://contract",
        internal_api_token="contract-secret",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"kind": "future_kind"})
        ),
    )
    try:
        with pytest.raises(BackendError) as captured:
            await client.get_workout_status(telegram_user_id=12345)
    finally:
        await client.close()
    assert isinstance(captured.value.__cause__, ValidationError)
