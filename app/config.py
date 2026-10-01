"""Application settings loaded from the environment (12-factor)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'Subsea Telemetry API'
    debug: bool = False

    database_url: str = 'postgresql+asyncpg://subsea:subsea@localhost:5432/subsea'

    # Readings older than this are rejected on ingestion to keep obviously broken
    # clocks from poisoning the time series.
    max_reading_age_days: int = 365

    # Hard cap on a single bulk ingestion request.
    max_batch_size: int = 5000


@lru_cache
def get_settings() -> Settings:
    return Settings()
