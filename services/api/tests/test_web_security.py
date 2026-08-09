import pytest
from pydantic import ValidationError

from app.api.rate_limit import RateLimiter
from app.application.web_auth import normalize_email
from app.config import Settings
from app.infrastructure.security import new_opaque_token, token_hash


def test_email_normalization_is_trimmed_and_casefolded() -> None:
    assert normalize_email("  Mario.Rossi@EXAMPLE.COM ") == "mario.rossi@example.com"


def test_opaque_tokens_are_random_and_only_the_hash_is_persistable() -> None:
    first = new_opaque_token()
    second = new_opaque_token()

    assert first != second
    assert len(token_hash(first)) == 64
    assert first not in token_hash(first)


def test_rate_limiter_rejects_after_configured_capacity() -> None:
    limiter = RateLimiter()

    assert limiter.allow("login:user", limit=2, window_seconds=60)
    assert limiter.allow("login:user", limit=2, window_seconds=60)
    assert not limiter.allow("login:user", limit=2, window_seconds=60)
    assert limiter.allow("login:other", limit=2, window_seconds=60)


def test_rate_limiter_bounds_the_number_of_buckets() -> None:
    limiter = RateLimiter(max_buckets=2)
    assert limiter.allow("one", limit=1, window_seconds=60)
    assert limiter.allow("two", limit=1, window_seconds=60)
    assert limiter.allow("three", limit=1, window_seconds=60)
    assert limiter.allow("one", limit=1, window_seconds=60)


def test_production_rejects_insecure_auth_configuration() -> None:
    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            database_url="postgresql+asyncpg://test:test@localhost/test",
            internal_api_token="internal",
            public_web_url="http://example.com",
            session_cookie_secure=False,
        )


def test_production_accepts_https_and_independent_secrets() -> None:
    settings = Settings(
        app_env="production",
        database_url="postgresql+asyncpg://test:test@localhost/test",
        internal_api_token="internal",
        public_web_url="https://app.example.com",
        session_cookie_secure=True,
        session_secret="s" * 32,
        csrf_secret="c" * 32,
        telegram_bot_username="jim007_prod_bot",
    )
    assert settings.session_cookie_secure is True
