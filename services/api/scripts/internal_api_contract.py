"""Export or check the Telegram bot's filtered internal OpenAPI contract."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


API_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = API_ROOT.parents[1]
CONTRACT_PATH = REPOSITORY_ROOT / "contracts" / "internal-api" / "v2" / "openapi.json"
INTERNAL_PATHS = (
    "/internal/telegram-link-requests/claim",
    "/internal/telegram-connections/resolve",
    "/internal/workout-events",
    "/internal/program-events",
    "/internal/workout-status",
    "/internal/history-queries",
)


def _load_openapi() -> dict[str, Any]:
    os.environ.setdefault("APP_ENV", "contract")
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+asyncpg://contract:contract@127.0.0.1:1/contract",
    )
    os.environ.setdefault("INTERNAL_API_TOKEN", "contract-secret")
    os.environ.setdefault("GEMINI_API_KEY", "contract-key")
    sys.path.insert(0, str(API_ROOT))

    from app.main import app

    return app.openapi()


def _schema_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, dict):
        reference = value.get("$ref")
        prefix = "#/components/schemas/"
        if isinstance(reference, str) and reference.startswith(prefix):
            names.add(reference.removeprefix(prefix))
        for child in value.values():
            names.update(_schema_names(child))
    elif isinstance(value, list):
        for child in value:
            names.update(_schema_names(child))
    return names


def filtered_openapi() -> dict[str, Any]:
    source = _load_openapi()
    paths = {path: source["paths"][path] for path in INTERNAL_PATHS}
    source_schemas = source.get("components", {}).get("schemas", {})
    required = _schema_names(paths)
    pending = list(required)
    while pending:
        name = pending.pop()
        for dependency in _schema_names(source_schemas[name]):
            if dependency not in required:
                required.add(dependency)
                pending.append(dependency)

    components: dict[str, Any] = {
        "schemas": {name: source_schemas[name] for name in sorted(required)}
    }
    security_schemes = source.get("components", {}).get("securitySchemes")
    if security_schemes:
        components["securitySchemes"] = security_schemes

    return {
        "openapi": source["openapi"],
        "info": source["info"],
        "paths": paths,
        "components": components,
    }


def serialized_contract() -> str:
    return json.dumps(
        filtered_openapi(), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    arguments = parser.parse_args()
    generated = serialized_contract()

    if arguments.write:
        CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONTRACT_PATH.write_text(generated, encoding="utf-8")
        print(f"Wrote {CONTRACT_PATH}")
        return 0

    if not CONTRACT_PATH.exists() or CONTRACT_PATH.read_text(encoding="utf-8") != generated:
        print(
            "Internal API contract is stale. Run "
            "`python scripts/internal_api_contract.py --write` from services/api.",
            file=sys.stderr,
        )
        return 1
    print("Internal API contract is current.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
