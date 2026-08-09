from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import TypeAdapter

sys.path.insert(0, str(Path(__file__).parents[1]))

from app.api.schemas import (
    ApiErrorResponse,
    HistoryQueryRequest,
    HistoryQueryResponse,
    ProgramEventRequest,
    ProgramEventResponse,
    WorkoutEventRequest,
    WorkoutEventResponse,
    WorkoutStatusRequest,
    WorkoutStatusResponse,
)
from app.api.telegram_links import (
    TelegramClaimRequest,
    TelegramInternalResponse,
    TelegramResolveRequest,
)
from scripts.internal_api_contract import CONTRACT_PATH, filtered_openapi, serialized_contract


pytestmark = pytest.mark.contract

MANIFEST_PATH = Path(__file__).parents[3] / "contracts" / "internal-api" / "v2" / "interactions.json"


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


def _kind_values(schema: object) -> set[str]:
    values: set[str] = set()
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            kind = properties.get("kind")
            if isinstance(kind, dict):
                if isinstance(kind.get("const"), str):
                    values.add(kind["const"])
                values.update(
                    item for item in kind.get("enum", []) if isinstance(item, str)
                )
        for child in schema.values():
            values.update(_kind_values(child))
    elif isinstance(schema, list):
        for child in schema:
            values.update(_kind_values(child))
    return values


def test_filtered_openapi_snapshot_is_current() -> None:
    assert CONTRACT_PATH.read_text(encoding="utf-8") == serialized_contract()


@pytest.mark.parametrize("interaction", _manifest()["interactions"], ids=lambda item: item["id"])
def test_provider_accepts_every_contract_interaction(interaction: dict[str, Any]) -> None:
    operation = interaction["operation"]
    body = interaction["request"]["body"]
    request_models = {
        "telegram_claim": TelegramClaimRequest,
        "telegram_resolve": TelegramResolveRequest,
        "workout_event": WorkoutEventRequest,
        "program_event": ProgramEventRequest,
        "status": WorkoutStatusRequest,
        "history": HistoryQueryRequest,
    }
    request_models[operation].model_validate(body)

    response = interaction["response"]
    if interaction["status"] >= 400:
        ApiErrorResponse.model_validate(response)
        return
    response_adapters = {
        "telegram_claim": TypeAdapter(TelegramInternalResponse),
        "telegram_resolve": TypeAdapter(TelegramInternalResponse),
        "workout_event": TypeAdapter(WorkoutEventResponse),
        "program_event": TypeAdapter(ProgramEventResponse),
        "status": TypeAdapter(WorkoutStatusResponse),
        "history": TypeAdapter(HistoryQueryResponse),
    }
    response_adapters[operation].validate_python(response)


def test_contract_covers_every_response_discriminator() -> None:
    interactions = _manifest()["interactions"]
    covered = {
        operation: {
            item["expected_kind"]
            for item in interactions
            if item["operation"] == operation and "expected_kind" in item
        }
        for operation in ("workout_event", "program_event", "status", "history")
    }
    expected = {
        "workout_event": _kind_values(WorkoutEventResponse.model_json_schema()),
        "program_event": _kind_values(ProgramEventResponse.model_json_schema()),
        "status": _kind_values(TypeAdapter(WorkoutStatusResponse).json_schema()),
        "history": _kind_values(TypeAdapter(HistoryQueryResponse).json_schema()),
    }
    assert all(covered[name] and covered[name] <= expected[name] for name in covered)


def test_internal_authentication_and_idempotency_are_documented() -> None:
    schema = filtered_openapi()
    assert schema["components"]["securitySchemes"]["HTTPBearer"] == {
        "scheme": "bearer",
        "type": "http",
    }
    idempotent_paths = {
        "/internal/workout-events", "/internal/program-events",
        "/internal/telegram-link-requests/claim",
    }
    for path, path_item in schema["paths"].items():
        operation = path_item["post"]
        assert operation["security"] == [{"HTTPBearer": []}]
        idempotency = [
            parameter
            for parameter in operation.get("parameters", [])
            if parameter["name"] == "Idempotency-Key"
        ]
        if path in idempotent_paths:
            assert len(idempotency) == 1
            assert idempotency[0]["in"] == "header"
            assert idempotency[0]["required"] is True
        else:
            assert idempotency == []


def test_every_interaction_status_is_documented_in_openapi() -> None:
    schema = filtered_openapi()
    for interaction in _manifest()["interactions"]:
        responses = schema["paths"][interaction["request"]["path"]]["post"]["responses"]
        assert str(interaction["status"]) in responses, interaction["id"]
