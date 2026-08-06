"""HTTP helpers for idempotent command endpoints."""

import hashlib
import json
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status


def hash_canonical_json(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def request_hash(
    operation: str,
    path_values: dict[str, UUID],
    payload: object,
) -> str:
    if hasattr(payload, "model_dump"):
        body = payload.model_dump(mode="json", exclude_none=False)  # type: ignore[attr-defined]
    else:
        body = payload
    return hash_canonical_json(
        {
            "operation": operation,
            "path": {key: str(value) for key, value in path_values.items()},
            "body": body,
        }
    )


def _idempotency_key(
    value: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
) -> str:
    value = value.strip()
    if not value:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "invalid_idempotency_key",
                "message": "Idempotency-Key must not be blank",
            },
        )
    return value


IdempotencyKey = Annotated[str, Depends(_idempotency_key)]
