"""Shared test setup.

Nothing in the test suite may touch the network or the real ``data/steam.db``:
the scraper thread is disabled before ``app`` is imported, and every test gets
its own empty SQLite database in a temp directory.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

# Must be set before `app` is imported anywhere.
os.environ["SCRAPER_ENABLED"] = "0"

import database  # noqa: E402

# `app` calls init_db() at import time; point it away from the real database
# even if a test module imports it outside of a fixture.
_import_time_dir = tempfile.mkdtemp(prefix="steamogames-tests-")
database.DB_DIR = _import_time_dir
database.DB_PATH = os.path.join(_import_time_dir, "import-time.db")


@pytest.fixture(autouse=True)
def temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "steam.db"))
    database.init_db()
    return tmp_path / "steam.db"


@pytest.fixture
def client():
    import app as app_module

    app_module.app.config.update(TESTING=True)
    return app_module.app.test_client()


@pytest.fixture
def ago():
    """Return an ISO timestamp (naive UTC, the format the app stores) in the past."""

    def _ago(**delta):
        moment = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(**delta)
        return moment.isoformat()

    return _ago
