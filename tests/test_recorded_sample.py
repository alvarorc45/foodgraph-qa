"""Does the pipeline survive payloads the API really sent?

Optional by design. The file only exists once someone runs
scripts/record_fixtures.py, and a fresh clone has to stay green.

Everything here asserts on parsing and loading, never on the data being
clean. Real OFF records contain real defects - that's the premise of the
project - so asserting they pass the quality checks would amount to
asserting the harness finds nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from foodgraph import ingest
from foodgraph.checks import ALL_CHECKS
from foodgraph.report import run_all

SAMPLE = Path(__file__).parent / "fixtures" / "recorded_sample.json"


def _load_sample() -> list[dict[str, Any]]:
    if not SAMPLE.exists():
        pytest.skip(
            "no recorded sample; run: "
            "python scripts/record_fixtures.py --search chocolate -n 10"
        )
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


def test_every_recorded_payload_normalises() -> None:
    """A payload the parser rejects is a gap in the parser, not in the data."""
    rejected = [
        payload.get("code")
        for payload in _load_sample()
        if ingest.normalise(payload) is None
    ]
    assert not rejected, f"the parser could not handle: {rejected}"


def test_recorded_sample_loads_without_crashing(db) -> None:
    payloads = _load_sample()
    counts = ingest.load(db, payloads, source="recorded")
    assert counts["loaded"] == len(payloads)


def test_every_check_runs_against_real_data(db) -> None:
    """Checks have to survive real data, pass or fail.

    Asserts they execute and report, not that they pass. A check that throws
    on a null nobody anticipated is broken. One that fails on a genuinely bad
    record is working.
    """
    ingest.load(db, _load_sample(), source="recorded")
    results = run_all(db)
    assert len(results) == len(ALL_CHECKS)
    for result in results:
        assert result.summary()
        # "the check itself failed to run" is how report.run_all reports a
        # crashed check; that is the only outcome not allowed here.
        assert "the check itself failed to run" not in result.description
