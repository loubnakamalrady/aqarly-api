from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
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

    # Where scripts/seed.py reads operations.json and properties.json: the
    # frontend repo's seed data, which it only ever reads.
    frontend_data_dir: Path = ROOT.parent / "aqarly" / "packages" / "core" / "data"

    # The staging lock. There is no sign-in yet (roadmap Phase 9), so a
    # deployed copy is closed behind one shared username and password: people
    # get the browser's login prompt, and the frontends send it with every call.
    # Unset (as on a laptop), everything is open.
    staging_user: str = "staging"
    staging_password: str | None = None

    @field_validator("database_url")
    @classmethod
    def _use_psycopg(cls, url: str) -> str:
        """Hosts hand out `postgresql://…` (or `postgres://…`); SQLAlchemy
        needs `postgresql+psycopg://…` to know which driver to use."""
        for prefix in ("postgresql://", "postgres://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url.removeprefix(prefix)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()
