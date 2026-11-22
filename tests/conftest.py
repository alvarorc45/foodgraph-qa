"""Shared fixtures.

The offline suite never touches the network, so everything here is
deterministic and runs in milliseconds. Tests that can fail because someone
else's server is slow stop being evidence of anything, and a suite people
re-run until it goes green is worth nothing. Live tests carry the `live`
marker and are excluded by default.
"""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest

from foodgraph import ingest

FIXTURES = Path(__file__).parent / "fixtures"

# Fixture payloads carry fixed timestamps, which would eventually drift past
# the staleness threshold and fail freshness for no good reason. Clean
# payloads get re-dated on load; staleness is tested by injecting it.
RECENT_AGE_DAYS = 30


def _load_fixture(name: str) -> list[dict[str, Any]]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def raw_payloads() -> list[dict[str, Any]]:
    """The fixture payloads exactly as recorded, untouched."""
    return _load_fixture("products.json")


@pytest.fixture
def clean_payloads(raw_payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Payloads that should pass every check."""
    recent = int(time.time()) - RECENT_AGE_DAYS * 86400
    payloads = copy.deepcopy(raw_payloads)
    for payload in payloads:
        payload["last_modified_t"] = recent
    return payloads


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """An empty graph database."""
    conn = ingest.connect(tmp_path / "graph.db")
    yield conn
    conn.close()


@pytest.fixture
def clean_db(db: sqlite3.Connection, clean_payloads: list[dict[str, Any]]) -> sqlite3.Connection:
    """A graph loaded only with well-formed data."""
    ingest.load(db, clean_payloads, source="fixtures")
    return db


@pytest.fixture
def product_codes(clean_db: sqlite3.Connection) -> list[str]:
    """Codes actually present in the loaded fixture.

    Tests take the row they poke from here instead of hardcoding a barcode.
    Name a specific product and the test quietly stops testing anything the
    day the fixtures change - the UPDATE matches no rows, no defect gets
    injected, and the check "passes".
    """
    rows = clean_db.execute("SELECT code FROM product ORDER BY code").fetchall()
    assert rows, "the fixture loaded no products"
    return [row["code"] for row in rows]


@pytest.fixture
def off_client():
    """Live client. Only used by tests marked `live`."""
    from foodgraph.client import OffClient

    return OffClient(user_agent="foodgraph-qa/0.1 (CI contract tests)")
