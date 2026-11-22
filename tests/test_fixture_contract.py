"""The fixtures are an input, and inputs get validated.

Several tests only mean anything if the data underneath has certain
properties. You can't prove a shared ingredient becomes one node when no
ingredient is shared. Without this file, shrinking the fixture set doesn't
produce an error - it produces tests that still pass while checking nothing,
which is worse than a red build.

If something here fails, fix the fixtures, not the tests.
"""

from __future__ import annotations

import sqlite3
from typing import Any

MIN_PRODUCTS = 5


def test_enough_products(clean_payloads: list[dict[str, Any]]) -> None:
    assert len(clean_payloads) >= MIN_PRODUCTS, (
        f"only {len(clean_payloads)} products in the fixture; at least "
        f"{MIN_PRODUCTS} are needed for the ratio-based checks to mean anything. "
        "Re-record with: python scripts/record_fixtures.py --search chocolate -n 10"
    )


def test_every_payload_has_the_contracted_fields(clean_payloads: list[dict[str, Any]]) -> None:
    """Missing fields here would make the schema-drift check fail for the
    wrong reason: a thin fixture, not a changed API."""
    from foodgraph.client import PRODUCT_FIELDS

    for payload in clean_payloads:
        missing = [f for f in PRODUCT_FIELDS if f not in payload]
        assert not missing, f"{payload.get('code')} is missing {missing}"


def test_some_ingredient_is_shared(clean_db: sqlite3.Connection) -> None:
    """At least one ingredient has to appear in two products, or
    test_shared_entities_are_one_node_not_many has nothing to assert on."""
    row = clean_db.execute(
        """
        SELECT dst_id, COUNT(DISTINCT src_id) AS products
        FROM edge
        WHERE rel = 'CONTAINS_INGREDIENT'
        GROUP BY dst_id
        HAVING products >= 2
        LIMIT 1
        """
    ).fetchone()
    assert row is not None, (
        "no ingredient is shared between two products. Pick fixtures with some "
        "overlap — products from one category normally share ingredients."
    )


def test_some_product_has_complete_macros(clean_db: sqlite3.Connection) -> None:
    """`energy_macro_consistency` examines nothing without full macro rows."""
    count = clean_db.execute(
        """
        SELECT COUNT(*) AS n FROM product
        WHERE energy_kcal_100g IS NOT NULL AND energy_kcal_100g >= 40
          AND proteins_100g IS NOT NULL
          AND carbohydrates_100g IS NOT NULL
          AND fat_100g IS NOT NULL
        """
    ).fetchone()["n"]
    assert count >= 1, "no product carries a full set of macros"


def test_the_curated_fixture_is_clean_by_construction(clean_db: sqlite3.Connection) -> None:
    """This set exists to answer "does a good load pass?", so it has to be
    good. Real recorded data belongs in recorded_sample.json, where finding
    defects is the point. Mix them and the suite goes red every time OFF
    gains a bad record, which says nothing about this code.
    """
    from foodgraph.checks import ALL_CHECKS

    failures = [c.name for c in ALL_CHECKS if not c.run(clean_db).passed]
    assert not failures, (
        f"the curated fixture no longer passes: {failures}. Either a check "
        "regressed or someone put real-world data in products.json."
    )
