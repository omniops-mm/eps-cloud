"""App configuration. Nothing else reads env vars."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", hide_input_in_errors=True)

    database_url: str
    secret_key: str
    secure_cookies: bool = False
    # IANA timezone name. Dates shown to the user are computed in this zone,
    # not in the server's clock, which runs UTC inside containers.
    tz: str = "Europe/Berlin"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton. Import this, not Settings() directly."""
    # BaseSettings fills required fields from the environment.
    return Settings()  # type: ignore[call-arg]
