from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The repo root, so `.env` is found whichever directory a command runs from.
ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Configuration, read from the environment and then from `.env`.

    A real environment variable wins over `.env`, which is how a deployed
    server (or a test) points the app at a different database.
    """

    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # e.g. postgresql+psycopg://aqarly:aqarly@localhost:5432/aqarly
    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
