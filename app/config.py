"""App configuration. Nothing else reads env vars."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    secret_key: str


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Import this, not Settings() directly."""
    # This is here so that mypy does not get in the way. whole point is to not have to write in env vars
    return Settings()  # type: ignore[call-arg]
