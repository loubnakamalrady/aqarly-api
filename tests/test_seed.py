"""scripts/seed.py's logic: loads the frontend's data, and refuses bad data
without leaving anything half-written."""

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Property, ServiceRequest, Unit
from app.seed import SeedError, seed
from app.settings import get_settings

DATA_DIR = get_settings().frontend_data_dir

pytestmark = pytest.mark.skipif(
    not (DATA_DIR / "operations.json").exists(),
    reason=f"frontend seed data not found at {DATA_DIR} (set FRONTEND_DATA_DIR)",
)


@pytest.fixture
def operations() -> dict:
    return json.loads((DATA_DIR / "operations.json").read_text())


@pytest.fixture
def edited_data(tmp_path: Path, operations: dict):
    """Returns a function that writes an edited copy of the seed data to a
    temporary folder and gives back its path. The real files are never touched."""

    def write(edit) -> Path:
        shutil.copy(DATA_DIR / "properties.json", tmp_path / "properties.json")
        edit(operations)
        (tmp_path / "operations.json").write_text(json.dumps(operations))
        return tmp_path

    return write


def test_loads_every_record_from_the_frontend(session: Session, operations: dict) -> None:
    counts = seed(session, DATA_DIR)

    assert counts["properties"] == len(operations["properties"])
    assert counts["units"] == len(operations["units"])
    assert counts["tenants"] == len(operations["tenants"])
    assert counts["staff"] == len(operations["staff"])
    assert counts["housekeeping_rates"] == len(operations["housekeepingRates"])
    assert counts["service_requests"] == len(operations["requests"])
    assert counts["stage_history"] == sum(len(r["stageHistory"]) for r in operations["requests"])


def test_computed_stage_matches_the_frontend_for_every_request(session: Session, operations: dict) -> None:
    seed(session, DATA_DIR)

    in_database = dict(session.execute(select(ServiceRequest.id, ServiceRequest.stage)).all())
    assert in_database == {r["id"]: r["stage"] for r in operations["requests"]}


def test_seeding_again_resets_rather_than_duplicates(session: Session) -> None:
    first = seed(session, DATA_DIR)
    session.delete(session.scalars(select(ServiceRequest)).first())
    session.flush()

    assert seed(session, DATA_DIR) == first


def test_refuses_a_stage_the_history_does_not_support(session: Session, edited_data) -> None:
    def claim_done(operations: dict) -> None:
        request = next(r for r in operations["requests"] if r["stage"] == "submitted")
        request["stage"] = "done"

    with pytest.raises(SeedError, match="seed says done"):
        seed(session, edited_data(claim_done))


def test_refuses_a_field_it_does_not_know(session: Session, edited_data) -> None:
    def add_field(operations: dict) -> None:
        operations["units"][0]["parkingSpot"] = "B12"

    with pytest.raises(SeedError, match="parkingSpot"):
        seed(session, edited_data(add_field))


def test_a_failed_seed_leaves_the_previous_data(session: Session, edited_data) -> None:
    # "Previous data" that a reload would overwrite: a renamed property.
    seed(session, DATA_DIR)
    session.get_one(Property, "prop-marina-heights").name = "Renamed before the reseed"
    session.commit()  # ends a savepoint; the test's outer transaction still rolls back

    def break_a_request(operations: dict) -> None:
        request = next(r for r in operations["requests"] if r["stage"] == "submitted")
        request["stage"] = "done"

    # Fails at the final check, after the tables were already emptied and
    # reloaded: the worst moment to fail.
    with pytest.raises(SeedError):
        seed(session, edited_data(break_a_request))
    session.rollback()

    assert session.get_one(Property, "prop-marina-heights").name == "Renamed before the reseed"
    assert session.scalar(select(func.count()).select_from(Unit)) == 70
