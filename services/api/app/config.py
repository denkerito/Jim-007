from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str
    internal_api_token: SecretStr
    llm_provider: str = "gemini"
    llm_model: str = "gemini-3.5-flash-lite"
    gemini_api_key: SecretStr | None = None
    llm_timeout_seconds: float = Field(default=8.0, gt=0, le=60)
    llm_max_output_tokens: int = Field(default=4096, gt=0, le=65536)
    llm_thinking_level: Literal["minimal", "low", "medium", "high"] = "minimal"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
