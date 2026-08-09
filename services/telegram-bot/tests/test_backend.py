import json
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

from app.backend import BackendClient, BackendError


def _response(status_code: int) -> httpx.Response:
    return httpx.Response(status_code, json={"kind": "linked", "user_id": None})


@pytest.mark.asyncio
async def test_telegram_resolution_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/telegram-connections/resolve"
        assert request.headers["Authorization"] == "Bearer internal-secret"
        assert json.loads(request.content) == {
            "telegram_user_id": 12345,
            "username": "first_name",
            "display_name": "First User",
        }
        return _response(200)

    client = BackendClient(
        base_url="http://api:8000/",
        internal_api_token="internal-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.resolve_telegram_connection(
            telegram_user_id=12345,
            username="first_name",
            display_name="First User",
        )
        assert result.kind == "linked"
    finally:
        await client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("handler", "cause_type"),
    [
        (
            lambda request: httpx.Response(
                503, json={"detail": {"code": "unavailable", "message": "down"}}
            ),
            BackendError,
        ),
        (
            lambda request: httpx.Response(200, json={"unexpected": "body"}),
            ValidationError,
        ),
    ],
)
async def test_resolution_rejects_backend_and_protocol_errors(
    handler, cause_type
) -> None:
    client = BackendClient(
        base_url="http://api:8000",
        internal_api_token="internal-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(BackendError) as captured:
            await client.resolve_telegram_connection(
                telegram_user_id=12345,
                username=None,
                display_name=None,
            )
        if cause_type is BackendError:
            assert captured.value.code == "unavailable"
        else:
            assert isinstance(captured.value.__cause__, cause_type)
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_resolution_wraps_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    client = BackendClient(
        base_url="http://api:8000",
        internal_api_token="internal-secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(BackendError):
            await client.resolve_telegram_connection(
                telegram_user_id=12345,
                username=None,
                display_name=None,
            )
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_process_workout_event_sends_identity_idempotency_and_parses_summary() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/workout-events"
        assert request.headers["Idempotency-Key"] == "telegram:update:99"
        assert request.headers["Authorization"] == "Bearer secret"
        assert json.loads(request.content) == {
            "provider": "telegram",
            "provider_subject": "12345",
            "action": "log",
            "text": "panca 80x8",
        }
        exercise = {
            "exercise": {"name": "Bench Press"},
            "sets": [
                {
                    "repetitions": 8,
                    "load": {"value": "80.000", "unit": "kg"},
                }
            ],
        }
        return httpx.Response(
            201,
            json={
                "kind": "logged",
                "replayed": False,
                "workout": {
                    "performed_on": "2026-08-05",
                    "exercises": [exercise],
                },
                "added_exercises": [exercise],
                "clarification_message": None,
            },
        )

    client = BackendClient(
        base_url="http://backend",
        internal_api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.process_workout_event(
            telegram_user_id=12345,
            update_id=99,
            action="log",
            text="panca 80x8",
        )
    finally:
        await client.close()

    assert result.kind == "logged"
    assert result.exercises[0].name == "Bench Press"
    assert result.exercises[0].sets[0].load_value == Decimal("80.000")
    assert result.total_exercises == 1
    assert result.total_sets == 1


@pytest.mark.asyncio
async def test_backend_requests_use_a_total_twelve_second_deadline(monkeypatch) -> None:
    deadlines: list[float] = []

    class RecordingTimeout:
        async def __aenter__(self) -> None:
            return None

        async def __aexit__(self, exc_type, exc_value, traceback) -> None:
            return None

    def record_timeout(seconds: float) -> RecordingTimeout:
        deadlines.append(seconds)
        return RecordingTimeout()

    monkeypatch.setattr("app.backend.asyncio.timeout", record_timeout)
    client = BackendClient(
        base_url="http://backend",
        internal_api_token="secret",
        transport=httpx.MockTransport(lambda request: _response(200)),
    )
    try:
        await client.resolve_telegram_connection(
            telegram_user_id=12345,
            username=None,
            display_name=None,
        )
    finally:
        await client.close()

    assert deadlines == [12.0]


@pytest.mark.asyncio
async def test_process_workout_event_preserves_backend_error_code() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={"detail": {"code": "noactiveworkout", "message": "missing"}},
        )

    client = BackendClient(
        base_url="http://backend",
        internal_api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(BackendError) as captured:
            await client.process_workout_event(
                telegram_user_id=12345,
                update_id=100,
                action="log",
                text="panca 80x8",
            )
    finally:
        await client.close()

    assert captured.value.code == "noactiveworkout"


@pytest.mark.asyncio
async def test_undo_parses_removed_exercises_and_updated_totals() -> None:
    exercise = {
        "exercise": {"name": "Bench Press"},
        "sets": [{"repetitions": 8, "load": {"value": "80", "unit": "kg"}}],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["action"] == "undo"
        return httpx.Response(
            200,
            json={
                "kind": "undone",
                "replayed": False,
                "workout": {
                    "performed_on": "2026-08-05",
                    "exercises": [],
                },
                "removed_exercises": [exercise],
            },
        )

    client = BackendClient(
        base_url="http://backend",
        internal_api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.process_workout_event(
            telegram_user_id=12345,
            update_id=101,
            action="undo",
            text=None,
        )
    finally:
        await client.close()

    assert result.kind == "undone"
    assert result.removed_exercises[0].name == "Bench Press"
    assert result.total_exercises == 0
    assert result.total_sets == 0


@pytest.mark.asyncio
async def test_get_active_workout_status_has_no_idempotency_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/workout-status"
        assert "Idempotency-Key" not in request.headers
        assert json.loads(request.content) == {
            "provider": "telegram",
            "provider_subject": "12345",
        }
        return httpx.Response(
            200,
            json={
                "kind": "active",
                "workout": {
                    "performed_on": "2026-08-05",
                    "notes": "Push day",
                    "exercises": [],
                },
            },
        )

    client = BackendClient(
        base_url="http://backend",
        internal_api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.get_workout_status(telegram_user_id=12345)
    finally:
        await client.close()

    assert result.kind == "active"
    assert result.workout is not None
    assert result.workout.notes == "Push day"


@pytest.mark.asyncio
async def test_query_exercise_history_sends_identity_and_parses_full_details() -> None:
    exercise_id = uuid4()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/history-queries"
        assert request.headers["Authorization"] == "Bearer secret"
        assert "Idempotency-Key" not in request.headers
        assert json.loads(request.content) == {
            "provider": "telegram",
            "provider_subject": "12345",
            "kind": "exercise",
            "query": "panca",
            "limit": 3,
        }
        return httpx.Response(
            200,
            json={
                "kind": "exercise",
                "exercise": {
                    "id": str(exercise_id),
                    "name": "Bench Press",
                    "normalized_name": "bench press",
                },
                "items": [
                    {
                        "workout_id": str(uuid4()),
                        "performed_on": "2026-08-05",
                        "workout_notes": "Push day",
                        "occurrences": [
                            {
                                "id": str(uuid4()),
                                "exercise": {
                                    "id": str(exercise_id),
                                    "name": "Bench Press",
                                    "normalized_name": "bench press",
                                },
                                "position": 1,
                                "notes": "Pausa al petto",
                                "sets": [
                                    {
                                        "id": str(uuid4()),
                                        "set_number": 1,
                                        "repetitions": 8,
                                        "load": {
                                            "value": "80.000",
                                            "unit": "kg",
                                            "kilograms": "80.000000",
                                        },
                                        "notes": "RPE 8",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "next_cursor": None,
            },
        )

    client = BackendClient(
        base_url="http://backend",
        internal_api_token="secret",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.query_history(
            telegram_user_id=12345,
            kind="exercise",
            query="panca",
            limit=3,
        )
    finally:
        await client.close()

    assert result.kind == "exercise"
    assert result.exercise_name == "Bench Press"
    occurrence = result.exercise_workouts[0].occurrences[0]
    assert occurrence.notes == "Pausa al petto"
    assert occurrence.sets[0].load_value == Decimal("80.000")
    assert occurrence.sets[0].notes == "RPE 8"
