"""The graph must be well formed after a normal load."""

from __future__ import annotations

import sqlite3

import pytest

from foodgraph.checks import GRAPH_CHECKS
from foodgraph.checks.graph import UNRESOLVED_SHARE
from foodgraph.checks.base import Severity


@pytest.mark.graph
@pytest.mark.parametrize("check", GRAPH_CHECKS, ids=lambda c: c.name)
def test_clean_graph_passes_every_integrity_check(check, clean_db: sqlite3.Connection) -> None:
    result = check.run(clean_db)
    assert result.passed, result.summary()


@pytest.mark.graph
def test_every_product_reaches_its_brand_and_categories(clean_db: sqlite3.Connection) -> None:
    """A traversal rather than a row count - the edges have to lead somewhere."""
    rows = clean_db.execute(
        """
        SELECT p.code,
               COUNT(DISTINCT CASE WHEN e.rel = 'HAS_BRAND' THEN e.dst_id END) AS brands,
               COUNT(DISTINCT CASE WHEN e.rel = 'IN_CATEGORY' THEN e.dst_id END) AS categories
        FROM product p
        LEFT JOIN edge e ON e.src_type = 'product' AND e.src_id = p.code
        GROUP BY p.code
        """
    ).fetchall()
    assert rows, "the fixture loaded no products at all"
    for row in rows:
        assert row["brands"] >= 1, f"{row['code']} has no brand"
        assert row["categories"] >= 1, f"{row['code']} has no category"


@pytest.mark.graph
def test_shared_entities_are_one_node_not_many(clean_db: sqlite3.Connection) -> None:
    """An ingredient used by two products is one node, not two.

    The property that makes this a graph rather than a pile of rows. With one
    ingredient node per product, "what else contains this?" returns nothing
    and never errors.
    """
    row = clean_db.execute(
        """
        SELECT dst_id, COUNT(DISTINCT src_id) AS products
        FROM edge
        WHERE rel = 'CONTAINS_INGREDIENT'
        GROUP BY dst_id
        ORDER BY products DESC, dst_id
        LIMIT 1
        """
    ).fetchone()
    assert row is not None, "the fixture produced no ingredient relationships"
    assert row["products"] >= 2, (
        "no ingredient is shared by two products, so this property is untested. "
        "See tests/test_fixture_contract.py — the fixture set needs overlap."
    )

    copies = clean_db.execute(
        "SELECT COUNT(*) AS n FROM ingredient WHERE id = ?", (row["dst_id"],)
    ).fetchone()["n"]
    assert copies == 1


@pytest.mark.graph
def test_error_severity_checks_have_zero_tolerance(clean_db: sqlite3.Connection) -> None:
    """Structural rules must not be satisfiable by "only a few are broken"."""
    for check in GRAPH_CHECKS:
        if check.severity is not Severity.ERROR:
            continue
        result = check.run(clean_db)
        assert result.offenders == 0, (
            f"{check.name} is an ERROR check and tolerates nothing: {result.summary()}"
        )


@pytest.mark.graph
def test_unresolved_share_is_a_judgement_worth_defending() -> None:
    """The threshold is the opinion in this rule, so pin it down.

    There's no published baseline for how much of a crowd-sourced vocabulary
    fails to resolve, so the number is a judgement. Asserted anyway -
    widening it to quieten a noisy report should need an argument, not a
    one-line diff.
    """
    assert 0.02 <= UNRESOLVED_SHARE <= 0.25
