from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str
    internal_api_token: SecretStr
    public_web_url: str = "http://localhost:3000"
    telegram_bot_username: str = Field(
        default="jim007_bot", pattern=r"^[A-Za-z0-9_]{5,32}$"
    )
    session_secret: SecretStr = Field(
        default=SecretStr("development-session-secret-change-me"), min_length=32
    )
    csrf_secret: SecretStr = Field(
        default=SecretStr("development-csrf-secret-change-me"), min_length=32
    )
    session_cookie_secure: bool = False
    session_ttl_seconds: int = Field(default=604800, ge=300, le=2592000)
    email_verification_ttl_seconds: int = Field(default=86400, ge=300, le=604800)
    password_reset_ttl_seconds: int = Field(default=1800, ge=300, le=86400)
    telegram_link_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    login_rate_limit_per_minute: int = Field(default=10, ge=1, le=1000)
    email_rate_limit_per_hour: int = Field(default=5, ge=1, le=1000)
    telegram_link_rate_limit_per_minute: int = Field(default=5, ge=1, le=1000)
    smtp_host: str = "mailpit"
    smtp_port: int = Field(default=1025, ge=1, le=65535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    email_from: str = "JIM007 <no-reply@jim007.local>"
    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    llm_max_output_tokens: int = Field(default=4096, gt=0, le=65536)
    llm_thinking_level: Literal["minimal", "low", "medium", "high"] = "minimal"
    llm_clarification_ttl_seconds: int = Field(default=900, ge=60, le=3600)

    @model_validator(mode="after")
    def production_auth_must_be_secure(self) -> "Settings":
        if self.app_env.casefold() == "production":
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE must be enabled in production")
            if not self.public_web_url.startswith("https://"):
                raise ValueError("PUBLIC_WEB_URL must use HTTPS in production")
            if "development-" in self.session_secret.get_secret_value() or "development-" in self.csrf_secret.get_secret_value():
                raise ValueError("Production session and CSRF secrets must be configured")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
