"""Import real buildings and units, tenants, staff or admins from a CSV file.

    uv run python scripts/import_data.py units     my-units.csv
    uv run python scripts/import_data.py tenants   my-tenants.csv
    uv run python scripts/import_data.py staff     my-staff.csv
    uv run python scripts/import_data.py admins    my-admins.csv

Add --dry-run to see what it would do without changing anything.

A CSV is what a spreadsheet saves as (Excel or Numbers: File → Save As /
Export → CSV; Google Sheets: File → Download → CSV). The first row names the
columns; scripts/import-examples/ has one file of each kind to start from.
Import units before tenants: a tenant's row names the unit they live in.

Adds what's new, updates what's there, never deletes. All or nothing: any
problem refuses the whole file, row by row, and changes nothing. Writes to
DATABASE_URL, like the seed script, so to import into staging:

    DATABASE_URL="$(cat .neon-url)" uv run python scripts/import_data.py units my-units.csv
"""

import argparse
import csv
import sys
from pathlib import Path

# Scripts run with scripts/ on the import path, not the repo root, so make
# `import app` work the same way it does for the API.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.engine import make_url  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.importer import KINDS, ImportRefused, import_rows  # noqa: E402
from app.settings import get_settings  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Import records from a CSV file.")
    parser.add_argument("kind", choices=KINDS, help="what the file holds")
    parser.add_argument("file", type=Path, help="the CSV file")
    parser.add_argument("--dry-run", action="store_true", help="show what would change, change nothing")
    args = parser.parse_args()

    try:
        # utf-8-sig: Excel starts a UTF-8 CSV with an invisible marker.
        with args.file.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            header = [name.strip().lower() for name in reader.fieldnames or []]
            reader.fieldnames = header
            rows = list(reader)
    except FileNotFoundError:
        print(f"No file at {args.file}", file=sys.stderr)
        return 1
    except UnicodeDecodeError:
        print(f"{args.file} isn't a UTF-8 CSV. Save it again as 'CSV UTF-8'.", file=sys.stderr)
        return 1

    database = make_url(get_settings().database_url).render_as_string(hide_password=True)
    print(f"Importing {args.kind} from {args.file}\n     into {database}\n", flush=True)

    with SessionLocal() as session:
        try:
            report = import_rows(session, args.kind, header, rows)
        except ImportRefused as refused:
            session.rollback()
            for problem in refused.problems:
                print(f"  ✗ {problem}", file=sys.stderr)
            print("\nNothing was changed. Fix the file and run it again.", file=sys.stderr)
            return 1
        if args.dry_run:
            session.rollback()
        else:
            session.commit()

    for line in report.added:
        print(f"  + {line}")
    for line in report.updated:
        print(f"  ~ {line}")
    print(
        f"\n{len(report.added)} added, {len(report.updated)} updated, {report.unchanged} unchanged."
        + (" Dry run: nothing was saved." if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
