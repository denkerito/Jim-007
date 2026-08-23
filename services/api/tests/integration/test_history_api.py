from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api.dependencies import (
    get_exercise_query_interpreter,
    get_uow_factory,
)
from app.application.commands import (
    ExerciseQueryInterpretation,
    ExerciseResolutionStatus,
)
from app.api.web_security import SESSION_COOKIE
from app.infrastructure.database.models import (
    AppUser,
    ExternalIdentity,
    WebAccount,
    WebSession,
)
from app.infrastructure.database.uow import SqlAlchemyUnitOfWork
from app.infrastructure.security import PasswordService, token_hash
from app.main import app


class FakeExerciseResolver:
    def __init__(self) -> None:
        self.result = ExerciseQueryInterpretation(
            status=ExerciseResolutionStatus.NOT_FOUND
        )
        self.calls = 0

    async def resolve_exercise(self, **kwargs):
        self.calls += 1
        return self.result


def _headers(key: str | None = None) -> dict[str, str]:
    values = {"Authorization": "Bearer integration-secret"}
    if key is not None:
        values["Idempotency-Key"] = key
    return values


async def _completed_workout(
    client: AsyncClient,
    *,
    user_id,
    performed_on: date,
    key: str,
    exercise_id: str | None,
    duplicate: bool = False,
) -> str:
    created = await client.post(
        f"/users/{user_id}/workouts",
        headers=_headers(f"{key}:create"),
        json={"performed_on": performed_on.isoformat(), "notes": f"Workout {key}"},
    )
    assert created.status_code == 201, created.text
    workout_id = created.json()["id"]
    exercise = (
        {"kind": "existing", "exercise_id": exercise_id}
        if exercise_id is not None
        else {"kind": "new", "name": "Bench Press"}
    )
    added = await client.post(
        f"/users/{user_id}/workouts/{workout_id}/exercises",
        headers=_headers(f"{key}:add:1"),
        json={
            "exercise": exercise,
            "notes": "Prima occorrenza",
            "sets": [
                {
                    "repetitions": 8,
                    "load_value": "80",
                    "notes": "Serie principale",
                }
            ],
        },
    )
    assert added.status_code == 201, added.text
    if exercise_id is None:
        exercise_id = added.json()["exercise"]["id"]
    if duplicate:
        repeated = await client.post(
            f"/users/{user_id}/workouts/{workout_id}/exercises",
            headers=_headers(f"{key}:add:2"),
            json={
                "exercise": {"kind": "existing", "exercise_id": exercise_id},
                "notes": "Seconda occorrenza",
                "sets": [{"repetitions": 6, "load_value": "85"}],
            },
        )
        assert repeated.status_code == 201, repeated.text
    completed = await client.post(
        f"/users/{user_id}/workouts/{workout_id}/complete",
        headers=_headers(f"{key}:complete"),
    )
    assert completed.status_code == 200, completed.text
    return exercise_id


async def _web_session(session_factory, user_id, *, suffix: str = "owner") -> str:
    raw = f"web-history-session-{suffix}"
    now = datetime.now(timezone.utc)
    email = f"{suffix}@example.com"
    async with session_factory() as session:
        session.add(
            WebAccount(
                user_id=user_id,
                email=email,
                normalized_email=email,
                password_hash=PasswordService().hash("a-secure-password"),
                email_verified_at=now,
            )
        )
        session.add(
            WebSession(
                user_id=user_id,
                token_hash=token_hash(raw),
                created_at=now,
                expires_at=now + timedelta(hours=1),
            )
        )
        await session.commit()
    return raw


@pytest.mark.asyncio
async def test_web_history_uses_session_identity_and_preserves_ownership(
    session_factory, user_id
) -> None:
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            exercise_id = await _completed_workout(
                client,
                user_id=user_id,
                performed_on=date(2026, 8, 10),
                key="web-one",
                exercise_id=None,
            )
            await _completed_workout(
                client,
                user_id=user_id,
                performed_on=date(2026, 8, 11),
                key="web-two",
                exercise_id=exercise_id,
            )
            draft = await client.post(
                f"/users/{user_id}/workouts",
                headers=_headers("web-draft:create"),
                json={"performed_on": "2026-08-12"},
            )
            assert draft.status_code == 201

            unauthorized = await client.get("/api/me/workouts")
            assert unauthorized.status_code == 401

            raw_session = await _web_session(session_factory, user_id)
            client.cookies.set(SESSION_COOKIE, raw_session)

            first = await client.get("/api/me/workouts", params={"limit": 1})
            assert first.status_code == 200, first.text
            assert [item["performed_on"] for item in first.json()["items"]] == [
                "2026-08-11"
            ]
            assert first.json()["next_cursor"]
            second = await client.get(
                "/api/me/workouts",
                params={"limit": 1, "cursor": first.json()["next_cursor"]},
            )
            assert [item["performed_on"] for item in second.json()["items"]] == [
                "2026-08-10"
            ]

            catalog = await client.get("/api/me/exercises")
            assert catalog.status_code == 200
            assert [(item["id"], item["name"]) for item in catalog.json()["items"]] == [
                (exercise_id, "Bench Press")
            ]

            history = await client.get(
                f"/api/me/exercises/{exercise_id}/history", params={"limit": 1}
            )
            assert history.status_code == 200
            assert history.json()["exercise"]["name"] == "Bench Press"
            assert history.json()["next_cursor"]

            invalid = await client.get("/api/me/workouts", params={"cursor": "!"})
            assert invalid.status_code == 422

            other_user = uuid4()
            async with session_factory() as database:
                database.add(AppUser(id=other_user))
                await database.commit()
            other_exercise_id = await _completed_workout(
                client,
                user_id=other_user,
                performed_on=date(2026, 8, 13),
                key="foreign",
                exercise_id=None,
            )
            foreign = await client.get(
                f"/api/me/exercises/{other_exercise_id}/history"
            )
            assert foreign.status_code == 404
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_paginated_workout_and_exercise_history(
    session_factory, user_id
) -> None:
    resolver = FakeExerciseResolver()
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    app.dependency_overrides[get_exercise_query_interpreter] = lambda: resolver
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            exercise_id = await _completed_workout(
                client,
                user_id=user_id,
                performed_on=date(2026, 8, 1),
                key="one",
                exercise_id=None,
                duplicate=True,
            )
            for day, key in ((2, "two"), (3, "three")):
                await _completed_workout(
                    client,
                    user_id=user_id,
                    performed_on=date(2026, 8, day),
                    key=key,
                    exercise_id=exercise_id,
                )

            draft = await client.post(
                f"/users/{user_id}/workouts",
                headers=_headers("draft:create"),
                json={"performed_on": "2026-08-04"},
            )
            assert draft.status_code == 201
            draft_add = await client.post(
                f"/users/{user_id}/workouts/{draft.json()['id']}/exercises",
                headers=_headers("draft:add"),
                json={
                    "exercise": {"kind": "existing", "exercise_id": exercise_id},
                    "sets": [{"repetitions": 5, "load_value": "90"}],
                },
            )
            assert draft_add.status_code == 201

            unauthorized = await client.get(f"/users/{user_id}/workouts")
            assert unauthorized.status_code == 401

            first = await client.get(
                f"/users/{user_id}/workouts",
                headers=_headers(),
                params={"limit": 2},
            )
            assert first.status_code == 200, first.text
            assert [item["performed_on"] for item in first.json()["items"]] == [
                "2026-08-03",
                "2026-08-02",
            ]
            assert all(item["status"] == "completed" for item in first.json()["items"])
            assert first.json()["next_cursor"]

            second = await client.get(
                f"/users/{user_id}/workouts",
                headers=_headers(),
                params={"limit": 2, "cursor": first.json()["next_cursor"]},
            )
            assert second.status_code == 200, second.text
            assert [item["performed_on"] for item in second.json()["items"]] == [
                "2026-08-01"
            ]
            assert second.json()["next_cursor"] is None

            exercise_first = await client.get(
                f"/users/{user_id}/exercises/{exercise_id}/history",
                headers=_headers(),
                params={"limit": 2},
            )
            assert exercise_first.status_code == 200, exercise_first.text
            assert exercise_first.json()["exercise"]["name"] == "Bench Press"
            assert [item["performed_on"] for item in exercise_first.json()["items"]] == [
                "2026-08-03",
                "2026-08-02",
            ]

            exercise_second = await client.get(
                f"/users/{user_id}/exercises/{exercise_id}/history",
                headers=_headers(),
                params={
                    "limit": 2,
                    "cursor": exercise_first.json()["next_cursor"],
                },
            )
            assert exercise_second.status_code == 200, exercise_second.text
            oldest = exercise_second.json()["items"][0]
            assert oldest["performed_on"] == "2026-08-01"
            assert len(oldest["occurrences"]) == 2
            assert oldest["occurrences"][0]["sets"][0]["notes"] == "Serie principale"

            invalid_cursor = await client.get(
                f"/users/{user_id}/workouts",
                headers=_headers(),
                params={"cursor": "%%%"},
            )
            assert invalid_cursor.status_code == 422
            assert invalid_cursor.json()["detail"]["code"] == "invalid_history_cursor"

            invalid_limit = await client.get(
                f"/users/{user_id}/workouts",
                headers=_headers(),
                params={"limit": 21},
            )
            assert invalid_limit.status_code == 422

            missing = await client.get(
                f"/users/{user_id}/exercises/{uuid4()}/history",
                headers=_headers(),
            )
            assert missing.status_code == 404

            async with session_factory() as session:
                session.add(
                    ExternalIdentity(
                        id=uuid4(),
                        user_id=user_id,
                        provider="telegram",
                        provider_subject="555",
                    )
                )
                await session.commit()

            exact = await client.post(
                "/internal/history-queries",
                headers=_headers(),
                json={
                    "provider": "telegram",
                    "provider_subject": "555",
                    "kind": "exercise",
                    "query": "bench press",
                },
            )
            assert exact.status_code == 200, exact.text
            assert exact.json()["kind"] == "exercise"
            assert resolver.calls == 0

            resolver.result = ExerciseQueryInterpretation(
                status=ExerciseResolutionStatus.MATCHED,
                exercise_id=exercise_id,
            )
            alias = await client.post(
                "/internal/history-queries",
                headers=_headers(),
                json={
                    "provider": "telegram",
                    "provider_subject": "555",
                    "kind": "exercise",
                    "query": "panca",
                    "limit": 1,
                },
            )
            assert alias.status_code == 200, alias.text
            assert alias.json()["exercise"]["name"] == "Bench Press"
            assert resolver.calls == 1

            resolver.result = ExerciseQueryInterpretation(
                status=ExerciseResolutionStatus.NEEDS_CLARIFICATION,
                clarification_message="Panca piana o inclinata?",
            )
            ambiguous = await client.post(
                "/internal/history-queries",
                headers=_headers(),
                json={
                    "provider": "telegram",
                    "provider_subject": "555",
                    "kind": "exercise",
                    "query": "spinta",
                },
            )
            assert ambiguous.status_code == 200
            assert ambiguous.json() == {
                "kind": "needs_clarification",
                "clarification_message": "Panca piana o inclinata?",
            }

            resolver.result = ExerciseQueryInterpretation(
                status=ExerciseResolutionStatus.NOT_FOUND
            )
            unknown = await client.post(
                "/internal/history-queries",
                headers=_headers(),
                json={
                    "provider": "telegram",
                    "provider_subject": "555",
                    "kind": "exercise",
                    "query": "salto con asta",
                },
            )
            assert unknown.status_code == 200
            assert unknown.json() == {"kind": "exercise_not_found"}

            resolver.result = ExerciseQueryInterpretation(
                status=ExerciseResolutionStatus.MATCHED,
                exercise_id=uuid4(),
            )
            invalid_selection = await client.post(
                "/internal/history-queries",
                headers=_headers(),
                json={
                    "provider": "telegram",
                    "provider_subject": "555",
                    "kind": "exercise",
                    "query": "panca misteriosa",
                },
            )
            assert invalid_selection.status_code == 502
            assert invalid_selection.json()["detail"]["code"] == "llm_invalid_response"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_cursor_is_stable_for_workouts_on_the_same_date(
    session_factory, user_id
) -> None:
    app.dependency_overrides[get_uow_factory] = lambda: lambda: SqlAlchemyUnitOfWork(
        session_factory
    )
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            exercise_id = await _completed_workout(
                client,
                user_id=user_id,
                performed_on=date(2026, 8, 5),
                key="same-date-one",
                exercise_id=None,
            )
            await _completed_workout(
                client,
                user_id=user_id,
                performed_on=date(2026, 8, 5),
                key="same-date-two",
                exercise_id=exercise_id,
            )

            first = await client.get(
                f"/users/{user_id}/workouts",
                headers=_headers(),
                params={"limit": 1},
            )
            second = await client.get(
                f"/users/{user_id}/workouts",
                headers=_headers(),
                params={"limit": 1, "cursor": first.json()["next_cursor"]},
            )

            assert first.status_code == 200
            assert second.status_code == 200
            assert first.json()["items"][0]["performed_on"] == "2026-08-05"
            assert second.json()["items"][0]["performed_on"] == "2026-08-05"
            assert first.json()["items"][0]["id"] != second.json()["items"][0]["id"]
            assert second.json()["next_cursor"] is None
    finally:
        app.dependency_overrides.clear()
