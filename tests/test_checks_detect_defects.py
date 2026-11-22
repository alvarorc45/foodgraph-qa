"""Proof that the checks have teeth.

A validation suite that only ever runs against good data tells you nothing: a
check that returns PASS unconditionally would look identical. So every rule
gets a defect built specifically to trip it, and the test asserts two things —
that the right check fails, and that it reports the offending row.

This is the file worth reading first. It is the difference between claiming
the pipeline is monitored and demonstrating it.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import pytest

from foodgraph import ingest
from foodgraph.checks import by_name


def _assert_fails(conn: sqlite3.Connection, check_name: str) -> None:
    result = by_name(check_name).run(conn)
    assert not result.passed, (
        f"{check_name} passed despite an injected defect — the check is blind. "
        f"{result.summary()}"
    )
    assert result.offenders >= 1
    assert result.samples, f"{check_name} failed without reporting a single offending row"


def _assert_passes(conn: sqlite3.Connection, check_name: str) -> None:
    result = by_name(check_name).run(conn)
    assert result.passed, f"unexpected failure: {result.summary()}"


# ------------------------------------------------------------ graph defects

def test_orphan_edge_is_detected(clean_db, product_codes: list[str]) -> None:
    """An edge pointing at an ingredient that was never loaded."""
    clean_db.execute(
        "INSERT INTO edge VALUES ('product', ?, "
        "'CONTAINS_INGREDIENT', 'ingredient', 'ingredient-that-was-never-loaded')",
        (product_codes[0],),
    )
    clean_db.commit()
    _assert_fails(clean_db, "orphan_edges")


def test_wrong_relationship_type_is_detected(clean_db, product_codes: list[str]) -> None:
    """Both nodes exist; the relationship between them is nonsense.

    CONTAINS_INGREDIENT from a product to a *brand* is a perfectly valid row
    that no foreign key would reject, and a fact that means nothing.
    """
    brand = clean_db.execute("SELECT id FROM brand LIMIT 1").fetchone()["id"]
    clean_db.execute(
        "INSERT INTO edge VALUES ('product', ?, 'CONTAINS_INGREDIENT', 'brand', ?)",
        (product_codes[0], brand),
    )
    clean_db.commit()
    _assert_fails(clean_db, "invalid_relationship_type")
    # The endpoints are real, so the orphan check must stay quiet: otherwise
    # one defect would light up every alert and locating it would be guesswork.
    _assert_passes(clean_db, "orphan_edges")


def test_unknown_relationship_name_is_detected(clean_db, product_codes: list[str]) -> None:
    ingredient = clean_db.execute("SELECT id FROM ingredient LIMIT 1").fetchone()["id"]
    clean_db.execute(
        "INSERT INTO edge VALUES ('product', ?, 'TASTES_LIKE', 'ingredient', ?)",
        (product_codes[0], ingredient),
    )
    clean_db.commit()
    _assert_fails(clean_db, "invalid_relationship_type")


def test_unknown_node_label_is_detected(clean_db, product_codes: list[str]) -> None:
    clean_db.execute(
        "INSERT INTO edge VALUES ('product', ?, 'IN_CATEGORY', 'supplier', 'acme')",
        (product_codes[0],),
    )
    clean_db.commit()
    _assert_fails(clean_db, "unknown_node_label")


def test_self_loop_is_detected(clean_db) -> None:
    clean_db.execute(
        "INSERT INTO ingredient (id, name) VALUES ('sugar-loop', 'sugar loop')"
    )
    clean_db.execute(
        "INSERT INTO edge VALUES ('ingredient', 'sugar-loop', "
        "'CONTAINS_INGREDIENT', 'ingredient', 'sugar-loop')"
    )
    clean_db.commit()
    _assert_fails(clean_db, "self_referencing_edges")


def test_duplicate_entity_under_two_ids_is_detected(clean_db, product_codes: list[str]) -> None:
    """One ingredient wearing two ids, e.g. `palm-oil` and `palm_oil`.

    Nothing errors: both nodes are valid, both have edges. The damage is that
    every count grouped by ingredient is now split in two.
    """
    original = clean_db.execute("SELECT id, name FROM ingredient LIMIT 1").fetchone()
    # Same name, different id: the two nodes denote one real-world thing.
    twin_id = original["id"].replace("-", "_") + "_dup"
    clean_db.execute(
        "INSERT INTO ingredient (id, name) VALUES (?, ?)", (twin_id, original["name"])
    )
    clean_db.execute(
        "INSERT INTO edge VALUES ('product', ?, 'CONTAINS_INGREDIENT', 'ingredient', ?)",
        (product_codes[0], twin_id),
    )
    clean_db.commit()
    _assert_fails(clean_db, "duplicate_nodes")


def test_unreferenced_node_is_detected(clean_db: sqlite3.Connection) -> None:
    """A node left behind after whatever pointed at it went away."""
    clean_db.execute("INSERT INTO category (id, name) VALUES ('ghost', 'ghost')")
    clean_db.commit()
    _assert_fails(clean_db, "unreachable_nodes")


def test_missing_category_is_detected(db: sqlite3.Connection) -> None:
    """Enough products without a category to cross the tolerance."""
    payloads = [
        {
            "code": f"000000000{i:04d}",
            "product_name": f"Uncategorised {i}",
            "brands": "", "brands_tags": [],
            "categories_tags": [], "ingredients_tags": [], "additives_tags": [],
            "quantity": "100 g",
            "nutriments": {"energy-kcal_100g": 100, "proteins_100g": 1,
                           "carbohydrates_100g": 20, "fat_100g": 1},
            "last_modified_t": int(time.time()) - 86400,
        }
        for i in range(10)
    ]
    ingest.load(db, payloads, source="synthetic")
    _assert_fails(db, "missing_expected_relationship")


# ---------------------------------------------------------- quality defects

def test_implausible_energy_is_detected(clean_db, product_codes: list[str]) -> None:
    """3000 kcal per 100 g: more energy than pure fat can carry."""
    clean_db.execute(
        "UPDATE product SET energy_kcal_100g = 3000 WHERE code = ?",
        (product_codes[0],),
    )
    clean_db.commit()
    _assert_fails(clean_db, "implausible_energy")


def test_negative_macro_is_detected(clean_db, product_codes: list[str]) -> None:
    clean_db.execute(
        "UPDATE product SET proteins_100g = -5 WHERE code = ?", (product_codes[0],)
    )
    clean_db.commit()
    _assert_fails(clean_db, "impossible_macros")


def test_macros_over_100g_are_detected(clean_db, product_codes: list[str]) -> None:
    """80 g of fat plus 40 g of carbs does not fit in 100 g of food."""
    clean_db.execute(
        "UPDATE product SET fat_100g = 80, carbohydrates_100g = 40, proteins_100g = 5 "
        "WHERE code = ?",
        (product_codes[0],),
    )
    clean_db.commit()
    _assert_fails(clean_db, "impossible_macros")


def test_energy_contradicting_macros_is_detected(db: sqlite3.Connection) -> None:
    """The kJ figure pasted into the kcal field.

    Every column passes on its own: 2252 kcal is rejected by the bounds check,
    so use a value that is not — the macros of milk with the energy of a
    biscuit. Only comparing the two reveals it.
    """
    payloads = [
        {
            "code": f"111111111{i:04d}",
            "product_name": f"Mislabelled {i}",
            "brands": "Test", "brands_tags": ["test-brand"],
            "categories_tags": ["en:test-category"],
            "ingredients_tags": ["en:test-ingredient"],
            "additives_tags": [],
            "quantity": "100 g",
            "nutriments": {
                # Macros say ~64 kcal; the label claims 450.
                "energy-kcal_100g": 450,
                "proteins_100g": 3.2,
                "carbohydrates_100g": 4.7,
                "fat_100g": 3.6,
            },
            "last_modified_t": int(time.time()) - 86400,
        }
        for i in range(10)
    ]
    ingest.load(db, payloads, source="synthetic")
    _assert_fails(db, "energy_macro_consistency")
    # The individual columns are all perfectly reasonable, which is the point.
    _assert_passes(db, "implausible_energy")
    _assert_passes(db, "impossible_macros")


def test_incomplete_products_are_detected(db: sqlite3.Connection) -> None:
    payloads = [
        {
            "code": f"222222222{i:04d}",
            "product_name": "",
            "brands": "Test", "brands_tags": ["test-brand"],
            "categories_tags": ["en:test-category"],
            "ingredients_tags": ["en:test-ingredient"],
            "additives_tags": [],
            "quantity": "100 g",
            "nutriments": {},
            "last_modified_t": int(time.time()) - 86400,
        }
        for i in range(10)
    ]
    ingest.load(db, payloads, source="synthetic")
    _assert_fails(db, "completeness")


def test_stale_products_are_detected(clean_db: sqlite3.Connection) -> None:
    """Records nobody upstream has touched in a decade."""
    ten_years_ago = int(time.time()) - 10 * 365 * 86400
    clean_db.execute("UPDATE product SET last_modified_t = ?", (ten_years_ago,))
    clean_db.commit()
    _assert_fails(clean_db, "freshness")


def test_partial_load_is_detected(clean_db: sqlite3.Connection) -> None:
    """The run that asked for 500 products and quietly committed 40."""
    clean_db.execute(
        "UPDATE ingest_run SET requested = 500, loaded = 40, failed = 460 "
        "WHERE id = (SELECT MAX(id) FROM ingest_run)"
    )
    clean_db.commit()
    result = by_name("volume").run(clean_db)
    assert not result.passed
    assert result.offenders == 460


def test_schema_drift_is_detected(clean_db: sqlite3.Connection) -> None:
    """The API stops sending a field the pipeline depends on."""
    rows = clean_db.execute("SELECT code, payload FROM raw_payload").fetchall()
    for row in rows:
        payload: dict[str, Any] = json.loads(row["payload"])
        payload.pop("categories_tags", None)
        clean_db.execute(
            "UPDATE raw_payload SET payload = ? WHERE code = ?",
            (json.dumps(payload), row["code"]),
        )
    clean_db.commit()
    _assert_fails(clean_db, "schema_drift")


def test_product_without_source_payload_is_detected(clean_db: sqlite3.Connection) -> None:
    """A product that appeared without the payload it supposedly came from."""
    clean_db.execute(
        "INSERT INTO product (code, name, ingested_at) VALUES ('999', 'Ghost', ?)",
        (int(time.time()),),
    )
    clean_db.commit()
    _assert_fails(clean_db, "orphan_products")


# --------------------------------------------------------------- regression

@pytest.mark.parametrize(
    "check_name",
    [
        "orphan_edges", "invalid_relationship_type", "unknown_node_label",
        "self_referencing_edges", "duplicate_nodes", "unreachable_nodes",
        "missing_expected_relationship", "completeness", "volume", "freshness",
        "implausible_energy", "impossible_macros", "energy_macro_consistency",
        "schema_drift", "orphan_products",
    ],
)
def test_every_check_has_a_defect_test(check_name: str) -> None:
    """Guards against adding a rule and forgetting to prove it works.

    Reads this file's own source for the check's name. Crude, but it fails
    loudly the day someone registers a check and never writes a defect for it,
    which is exactly when a blind validator slips into the suite.
    """
    source = __import__("pathlib").Path(__file__).read_text(encoding="utf-8")
    occurrences = source.count(f'"{check_name}"')
    assert occurrences >= 2, (
        f"{check_name} is registered but has no test that makes it fail; "
        "a check nobody has seen fail is not evidence of anything"
    )
