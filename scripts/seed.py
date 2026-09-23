"""Wipe the database and reload the frontend's seed data.

    uv run python scripts/seed.py

Replaces the frontend's "Reset demo data" button. Reads FRONTEND_DATA_DIR
(default: ../aqarly/packages/core/data) and writes to DATABASE_URL. All or
nothing: if anything fails, the database is left as it was.
"""

import sys
from pathlib import Path

# Scripts run with scripts/ on the import path, not the repo root, so make
# `import app` work the same way it does for the API.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import make_url  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.seed import SeedError, seed  # noqa: E402
from app.settings import get_settings  # noqa: E402


def main() -> int:
    settings = get_settings()
    database = make_url(settings.database_url).render_as_string(hide_password=True)
    print(f"Seeding {database}\n   from {settings.frontend_data_dir}", flush=True)

    with SessionLocal() as session:
        try:
            counts = seed(session, settings.frontend_data_dir)
        except SeedError as e:
            session.rollback()
            print(f"\nNothing was changed. {e}", file=sys.stderr)
            return 1
        session.commit()

    width = max(map(len, counts))
    for table, count in counts.items():
        print(f"  {table:<{width}}  {count:>4}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
