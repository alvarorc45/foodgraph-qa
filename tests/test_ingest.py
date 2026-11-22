"""Unit tests for normalisation: the part most likely to corrupt data quietly.

Parsing bugs do not raise. They produce a number that is wrong by a factor of
four, or a None where a zero belonged, and everything downstream keeps working
on top of it. These are the tests that pin that behaviour down.
"""

from __future__ import annotations

import pytest

from foodgraph.ingest import _clean_tag, _energy_kcal, _number, normalise


@pytest.mark.parametrize(
    "tag, expected",
    [
        ("en:breakfast-cereals", ("breakfast-cereals", "breakfast cereals")),
        # Language prefixes differ while the concept does not; keeping the
        # prefix would create one node per language.
        ("fr:cereales", ("cereales", "cereales")),
        ("no-prefix", ("no-prefix", "no prefix")),
        ("  en:Sugar  ", ("sugar", "sugar")),
        ("en:", None),
        ("", None),
    ],
)
def test_tag_parsing(tag, expected) -> None:
    assert _clean_tag(tag) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (12, 12.0),
        (3.5, 3.5),
        ("7.25", 7.25),
        # Open Food Facts contains comma decimals from European contributors.
        ("7,25", 7.25),
        ("", None),
        ("  ", None),
        ("unknown", None),
        (None, None),
        # float(True) is 1.0, which would invent a nutrient value out of a flag.
        (True, None),
        (False, None),
    ],
)
def test_number_coercion(value, expected) -> None:
    assert _number(value) == expected


def test_energy_prefers_kcal_over_kilojoules() -> None:
    assert _energy_kcal({"energy-kcal_100g": 100, "energy-kj_100g": 2000}) == 100


def test_energy_falls_back_to_converting_kilojoules() -> None:
    """Reporting nothing when only kJ is present would throw away a real value."""
    assert _energy_kcal({"energy-kj_100g": 418.4}) == pytest.approx(100.0, abs=0.1)


def test_energy_is_none_when_absent() -> None:
    assert _energy_kcal({}) is None


def test_normalise_builds_nodes_and_edges() -> None:
    record = normalise(
        {
            "code": "123",
            "product_name": "Test",
            "brands_tags": ["acme"],
            "categories_tags": ["en:snacks"],
            "ingredients_tags": ["en:sugar"],
            "additives_tags": ["en:e322"],
            "nutriments": {"energy-kcal_100g": 200, "proteins_100g": 1},
        }
    )
    assert record is not None
    assert record.code == "123"
    assert {n.label for n in record.nodes} == {"brand", "category", "ingredient", "additive"}
    assert {e.rel for e in record.edges} == {
        "HAS_BRAND", "IN_CATEGORY", "CONTAINS_INGREDIENT", "CONTAINS_ADDITIVE",
    }
    assert all(e.src_id == "123" for e in record.edges)


def test_normalise_rejects_payload_without_barcode() -> None:
    """No identity means no node anything could ever be traced back to."""
    assert normalise({"product_name": "Nameless"}) is None
    assert normalise({"code": "   "}) is None


def test_normalise_survives_malformed_fields() -> None:
    """The API is crowd-sourced; the loader must not assume well-formed input."""
    record = normalise(
        {
            "code": "456",
            "product_name": None,
            "categories_tags": "not-a-list",
            "ingredients_tags": [None, 42, "en:salt"],
            "nutriments": "not-a-dict",
        }
    )
    assert record is not None
    assert record.name is None
    assert record.energy_kcal_100g is None
    assert [e.dst_id for e in record.edges] == ["salt"]


def test_load_is_idempotent(db, clean_payloads) -> None:
    """Re-ingesting the same batch must not duplicate nodes or edges."""
    from foodgraph import ingest

    ingest.load(db, clean_payloads, source="first")
    first = {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("product", "brand", "category", "ingredient", "edge")
    }

    ingest.load(db, clean_payloads, source="second")
    second = {
        table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        for table in ("product", "brand", "category", "ingredient", "edge")
    }
    assert first == second
