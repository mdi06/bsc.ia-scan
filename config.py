"""Application settings loaded from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Optional runtime configuration for the API server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: str = "development"
    posthog_project_token: str | None = None
    posthog_host: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings for dependency-friendly overrides."""
    return Settings()
