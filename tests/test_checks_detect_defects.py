"""Proof that the checks have teeth.

A suite that only ever runs against good data tells you nothing, since a
rule hardcoded to return PASS would look identical. So every rule gets a
defect built to trip it, and each test asserts two things - that the right
check fails, and that it reports the offending row.

Probably the file to read first.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import pytest

from foodgraph import ingest
from foodgraph.checks import by_name
from foodgraph.checks.quality import DRIFT_MIN_PAYLOADS


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
    """Both nodes exist and the relationship between them is nonsense.

    CONTAINS_INGREDIENT from a product to a brand is a row no foreign key
    would reject and a fact that means nothing.
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
    """One ingredient wearing two ids, `palm-oil` and `palm_oil`.

    Nothing errors. Both nodes are valid and both have edges; the damage is
    that every count grouped by ingredient is now split in two.
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


def test_unresolved_entities_are_detected(db: sqlite3.Connection) -> None:
    """Raw contributor text entering the graph as if it were an entity.

    Taken from a real payload. OFF resolved `corn` and `sugar` against its
    taxonomy and left `casejnato-de-sqdio` carrying its original `es:`
    prefix because it matched nothing. Both end up as nodes and look
    identical in the graph; only the prefix says which one is real.
    """
    payloads = [
        {
            "code": f"555555555{i:04d}",
            "product_name": f"Snack {i}",
            "brands": "Test", "brands_tags": ["test-brand"],
            "categories_tags": ["en:snacks"],
            "ingredients_tags": [
                "en:corn", "en:sugar",
                "es:casejnato-de-sqdio",
                "es:contiene-ingredientes-de-huevo",
            ],
            "additives_tags": [],
            "quantity": "100 g",
            "nutriments": {"energy-kcal_100g": 500, "proteins_100g": 6,
                           "carbohydrates_100g": 60, "fat_100g": 25},
            "last_modified_t": int(time.time()) - 86400,
        }
        for i in range(10)
    ]
    ingest.load(db, payloads, source="synthetic")
    _assert_fails(db, "unresolved_entities")

    # The two well-formed ingredients mustn't come along for the ride. A
    # check that flags everything is as useless as one that flags nothing.
    offenders = by_name("unresolved_entities").run(db).samples
    flagged = {row["id"] for row in offenders}
    assert "corn" not in flagged and "sugar" not in flagged


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

    Every column passes on its own. 2252 kcal would trip the bounds check, so
    use something that doesn't - the macros of milk with the energy of a
    biscuit. Only comparing the two finds it.
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


def test_schema_drift_is_detected(db: sqlite3.Connection) -> None:
    """The API stops sending a field we depend on.

    Needs a real sample. Below DRIFT_MIN_PAYLOADS the check declines to
    answer, since one sparse product looks the same as a vanished field.
    """
    payloads = [
        {
            "code": f"444444444{i:04d}",
            "product_name": f"Product {i}",
            "brands": "Test", "brands_tags": ["test-brand"],
            "categories_tags": ["en:test-category"],
            "ingredients_tags": ["en:test-ingredient"],
            "additives_tags": [],
            "quantity": "100 g",
            "nutriments": {"energy-kcal_100g": 100, "proteins_100g": 1,
                           "carbohydrates_100g": 20, "fat_100g": 1},
            "last_modified_t": int(time.time()) - 86400,
        }
        for i in range(DRIFT_MIN_PAYLOADS)
    ]
    ingest.load(db, payloads, source="synthetic")
    clean_db = db
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


def test_a_tiny_run_does_not_report_drift(db: sqlite3.Connection) -> None:
    """Regression. One barcode used to break the build.

    `--barcodes <code>` is the reproduction step every bug report here tells
    a reader to run. OFF omits a field when the product has nothing to put in
    it, so one product with no additives made `additives_tags` absent from
    100% of payloads and schema_drift failed the build on sound data.
    """
    ingest.load(db, [{
        "code": "7501003101761",
        "product_name": "Salsa de cacahuate picante cremosa",
        "brands": "Test", "brands_tags": ["test-brand"],
        "categories_tags": ["en:sauces"],
        "ingredients_tags": ["en:peanut"],
        # No additives, so the API would not send the key at all.
        "quantity": "240 g",
        "nutriments": {"energy-kcal_100g": 700, "proteins_100g": 12.7,
                       "carbohydrates_100g": 26.7, "fat_100g": 60.7},
        "last_modified_t": int(time.time()) - 86400,
    }], source="one-barcode")

    result = by_name("schema_drift").run(db)
    assert result.passed, f"a one-product run must not claim drift: {result.summary()}"
    assert "not checked" in result.description, (
        "a green tick that means 'cannot tell' must say so, or it reads as "
        "'the contract holds'"
    )


# --------------------------------------------------------------- regression

@pytest.mark.parametrize(
    "check_name",
    [
        "orphan_edges", "invalid_relationship_type", "unknown_node_label",
        "self_referencing_edges", "duplicate_nodes", "unreachable_nodes",
        "missing_expected_relationship", "unresolved_entities",
        "completeness", "volume", "freshness",
        "implausible_energy", "impossible_macros", "energy_macro_consistency",
        "schema_drift", "orphan_products",
    ],
)
def test_every_check_has_a_defect_test(check_name: str) -> None:
    """Guards against adding a rule and forgetting to prove it works.

    Greps this file's own source for the check name. Crude, but it fails
    loudly the day someone registers a rule and never writes a defect for it,
    which is exactly when a blind validator slips in.
    """
    source = __import__("pathlib").Path(__file__).read_text(encoding="utf-8")
    occurrences = source.count(f'"{check_name}"')
    assert occurrences >= 2, (
        f"{check_name} is registered but has no test that makes it fail; "
        "a check nobody has seen fail is not evidence of anything"
    )
