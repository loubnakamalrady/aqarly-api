"""Empty the operation's data, to import real data in its place.

    uv run python scripts/clear_operations.py

Removes every building and unit, tenant, member of staff, admin, request (with
its history and photos), registration and sign-in. Keeps the housekeeping
rate card and the marketing site's listings. Asks you to type "clear" first.

Afterwards nobody can sign in anywhere until you import people, so run the
imports straight after, in this order (see scripts/import_data.py):

    uv run python scripts/import_data.py units   units.csv
    uv run python scripts/import_data.py tenants tenants.csv
    uv run python scripts/import_data.py staff   staff.csv
    uv run python scripts/import_data.py admins  admins.csv

On staging, put DATABASE_URL="$(cat .neon-url)" in front of each command.
The demo data comes back with scripts/seed.py, which wipes everything again.
"""

import sys
from pathlib import Path

# Scripts run with scripts/ on the import path, not the repo root, so make
# `import app` work the same way it does for the API.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import make_url  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.importer import KEPT_TABLES, clear_operations  # noqa: E402
from app.settings import get_settings  # noqa: E402


def main() -> int:
    database = make_url(get_settings().database_url).render_as_string(hide_password=True)
    print(f"This empties {database}")
    print(f"of everything but {' and '.join(KEPT_TABLES)}. It can't be undone.")
    if input('Type "clear" to go ahead: ').strip() != "clear":
        print("Nothing was changed.")
        return 1

    with SessionLocal() as session:
        counts = clear_operations(session)
        session.commit()

    width = max(map(len, counts))
    print("\nRemoved:")
    for table, count in counts.items():
        print(f"  {table:<{width}}  {count:>4}")
    print("\nNobody can sign in until you import admins, staff and tenants.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
