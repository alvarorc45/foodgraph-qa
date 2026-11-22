"""Quality rules, and the thresholds they hang on."""

from __future__ import annotations

import sqlite3
import time

import pytest

from foodgraph.checks import QUALITY_CHECKS
from foodgraph.checks.quality import (
    ENERGY_TOLERANCE,
    FIELD_ABSENT_SHARE,
    MACRO_SUM_LIMIT,
    MAX_KCAL_100G,
)
from foodgraph.report import to_html, to_markdown


@pytest.mark.quality
@pytest.mark.parametrize("check", QUALITY_CHECKS, ids=lambda c: c.name)
def test_clean_data_passes_every_quality_check(check, clean_db: sqlite3.Connection) -> None:
    result = check.run(clean_db)
    assert result.passed, result.summary()


@pytest.mark.quality
def test_thresholds_are_physically_sensible() -> None:
    """The constants are the judgement in this harness, so they get asserted.

    If someone relaxes MAX_KCAL_100G to 5000 to silence an alert, that is a
    decision worth failing a build over, not a config tweak.
    """
    # Pure fat is ~9 kcal/g, so 100 g cannot exceed ~900 kcal.
    assert 800 <= MAX_KCAL_100G <= 950
    # Macros live in 100 g of food; the slack is for rounding, not for physics.
    assert 100 <= MACRO_SUM_LIMIT <= 105
    # Wide enough for fibre and polyols, tight enough to catch a unit mix-up.
    assert 0.15 <= ENERGY_TOLERANCE <= 0.40


@pytest.mark.quality
def test_results_carry_evidence(clean_db: sqlite3.Connection) -> None:
    """Every result must be actionable: counts, a denominator, a description."""
    for check in QUALITY_CHECKS:
        result = check.run(clean_db)
        assert result.name == check.name
        assert result.description
        assert result.examined >= 0
        assert 0.0 <= result.ratio <= 1.0
        assert result.summary()


@pytest.mark.quality
def test_drift_threshold_is_per_field_and_strict() -> None:
    """A field either still arrives or it does not.

    Drift is not a statistical tail, so the share that counts as "gone" sits
    just under 100%. The margin exists only because a payload can legitimately
    omit an empty field; it is not room to absorb a real schema change.
    """
    assert 0.80 <= FIELD_ABSENT_SHARE < 1.0


@pytest.mark.quality
def test_a_tolerated_result_is_not_reported_as_clean(db: sqlite3.Connection) -> None:
    """Offending rows inside the tolerance must still be visible.

    This is the regression test for a report that printed `[PASS] 8/50` —
    identical in shape to `[PASS] 0/1841`. The build was right to stay green;
    the output was wrong to say nothing. A tolerance that silences the finding
    as well as the failure is just a blind spot with a config value.
    """
    from foodgraph import ingest
    from foodgraph.checks import by_name

    # Nine complete products and one with neither name nor energy: 10% of the
    # set, comfortably inside completeness' 20% tolerance.
    payloads = [
        {
            "code": f"333333333{i:04d}",
            "product_name": "" if i == 0 else f"Product {i}",
            "brands": "Test", "brands_tags": ["test-brand"],
            "categories_tags": ["en:test-category"],
            "ingredients_tags": ["en:test-ingredient"],
            "additives_tags": [],
            "quantity": "100 g",
            "nutriments": {} if i == 0 else {
                "energy-kcal_100g": 100, "proteins_100g": 1,
                "carbohydrates_100g": 20, "fat_100g": 1,
            },
            "last_modified_t": int(time.time()) - 86400,
        }
        for i in range(10)
    ]
    ingest.load(db, payloads, source="synthetic")

    result = by_name("completeness").run(db)
    assert result.passed, "10% incomplete should sit inside the tolerance"
    assert result.offenders == 1
    assert result.tolerated, "a tolerated finding must be distinguishable from a clean one"
    assert "ALLOW" in result.summary()
    assert "PASS" not in result.summary()
    assert result.samples, "the tolerated rows must still come with evidence"

    # And the renderers must carry that distinction through, or the whole
    # point is lost the moment anyone reads the report instead of the object.
    assert "## Tolerated" in to_markdown([result])
    assert "allow" in to_html([result])
