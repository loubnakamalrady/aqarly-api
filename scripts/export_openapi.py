"""Write the API's OpenAPI schema to openapi.json.

    uv run python scripts/export_openapi.py

The frontend generates `packages/core`'s API types from this file, so re-run
it after changing any route or schema. tests/test_openapi.py fails until you
do.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.main import app  # noqa: E402

OUTPUT = ROOT / "openapi.json"


def render() -> str:
    return json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUTPUT.write_text(render())
    print(f"Wrote {OUTPUT.relative_to(ROOT)}")
