import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_openapi_json_is_up_to_date() -> None:
    """openapi.json is what the frontend's types are generated from. If this
    fails, run `uv run python scripts/export_openapi.py` and commit the file."""
    spec = importlib.util.spec_from_file_location("export_openapi", ROOT / "scripts" / "export_openapi.py")
    assert spec and spec.loader
    export = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(export)

    assert (ROOT / "openapi.json").read_text() == export.render()
