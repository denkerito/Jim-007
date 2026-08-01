from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(case_sensitive=False)

    app_env: str = "development"
    log_level: str = "INFO"
    database_url: str
    internal_api_token: SecretStr
    llm_provider: str
    llm_model: str
    llm_api_key: SecretStr
    llm_base_url: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
