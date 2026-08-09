from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"
    telegram_bot_token: SecretStr
    telegram_mode: Literal["polling", "webhook"] = "polling"
    backend_base_url: str
    internal_api_token: SecretStr
    public_web_url: str = "http://localhost:3000"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
