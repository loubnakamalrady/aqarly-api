"""Shared test setup. pytest loads this before any test module.

Tests run against their own database, `<dev database>_test`, recreated from the
migrations at the start of every run, so they never touch the data you develop
against and every run also proves the migrations build the schema.
"""

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from app.settings import ROOT, Settings, get_settings

# This must happen before anything calls `get_settings()` (importing `app.db`
# or `app.main` does), which is why it runs at import time rather than in a
# fixture: conftest.py is imported before the test modules are.
_dev_url = make_url(Settings().database_url)
TEST_URL: URL = _dev_url.set(database=f"{_dev_url.database}_test")
os.environ["DATABASE_URL"] = TEST_URL.render_as_string(hide_password=False)
get_settings.cache_clear()


@pytest.fixture(scope="session", autouse=True)
def test_database() -> Iterator[None]:
    """Drop and recreate the test database, then migrate it to head."""
    # CREATE/DROP DATABASE can't run inside a transaction, hence AUTOCOMMIT,
    # and can't target the database you're connected to, hence `postgres`.
    admin = create_engine(TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    name = TEST_URL.database
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    admin.dispose()

    command.upgrade(Config(str(ROOT / "alembic.ini")), "head")
    yield


@pytest.fixture
def session() -> Iterator[Session]:
    """A session whose work is rolled back when the test ends.

    Everything runs inside one outer transaction that is never committed.
    `create_savepoint` means a `session.commit()` or a failed flush inside a
    test only ends a savepoint, not the outer transaction.
    """
    from app.db import engine

    with engine.connect() as connection:
        transaction = connection.begin()
        db = Session(bind=connection, join_transaction_mode="create_savepoint")
        try:
            yield db
        finally:
            db.close()
            transaction.rollback()
