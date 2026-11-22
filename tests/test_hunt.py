"""The sweep runs against a stub client, not the live API.

A survey tool you can only test by pointing it at the internet is a tool
nobody tests. `sweep` takes a client, so it runs offline in milliseconds and
the aggregation can be asserted against known inputs - including the case
that matters, a term whose pull fails halfway through.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from foodgraph.client import OffError  # noqa: E402

import hunt  # noqa: E402


def payload(code: str, **overrides: Any) -> dict[str, Any]:
    base = {
        "code": code,
        "product_name": f"Product {code}",
        "brands": "Marca",
        "brands_tags": ["marca"],
        "categories_tags": ["en:test-category"],
        "ingredients_tags": ["en:sugar"],
        "additives_tags": [],
        "quantity": "100 g",
        "nutriments": {
            "energy-kcal_100g": 400, "proteins_100g": 5,
            "carbohydrates_100g": 50, "fat_100g": 18,
        },
        "last_modified_t": int(time.time()) - 86400,
    }
    base.update(overrides)
    return base


class StubClient:
    """Three slices: one clean, one with a real defect, one that 503s."""

    def search(self, term: str, page_size: int = 50) -> list[dict[str, Any]]:
        if term == "upstream-down":
            raise OffError("HTTP 503")
        if term == "dirty":
            return [
                payload(f"9{i:012d}",
                        nutriments={"energy-kcal_100g": 4000, "proteins_100g": 5,
                                    "carbohydrates_100g": 50, "fat_100g": 18})
                for i in range(5)
            ]
        return [payload(f"8{i:012d}") for i in range(5)]


@pytest.fixture
def swept(tmp_path: Path) -> list[hunt.Outcome]:
    return hunt.sweep(
        StubClient(),
        ["clean", "dirty", "upstream-down"],
        limit=5,
        out=tmp_path / "out",
        work_dir=tmp_path / "work",
    )


def test_every_term_produces_an_outcome(swept: list[hunt.Outcome]) -> None:
    assert [o.term for o in swept] == ["clean", "dirty", "upstream-down"]


def test_a_failing_term_does_not_end_the_sweep(swept: list[hunt.Outcome]) -> None:
    """The test worth having here.

    OFF returns 503 often enough that a sweep aborting on the first one would
    never finish, and whoever ran it would conclude the dataset is clean
    because they never got past term three.
    """
    down = swept[-1]
    assert down.error, "the unreachable term must record why"
    assert down.findings == 0
    assert swept[1].findings > 0, "terms after the failure must still be swept"


def test_defects_are_attributed_to_the_right_term(swept: list[hunt.Outcome]) -> None:
    clean, dirty = swept[0], swept[1]
    assert [r.name for r in dirty.errors] == ["implausible_energy"]
    assert not clean.errors


def test_tolerated_findings_count_as_findings(tmp_path: Path) -> None:
    """A rule that absorbed broken rows still found them.

    Rank a tolerated finding as nothing and the sweep sends you to the wrong
    slice - the quiet one rather than the one with rows to look at.
    """
    class MostlyIncomplete:
        def search(self, term: str, page_size: int = 50) -> list[dict[str, Any]]:
            # 1 in 10 with no name and no energy: inside completeness' tolerance.
            return [
                payload(f"7{i:012d}",
                        product_name="" if i == 0 else f"Product {i}",
                        nutriments={} if i == 0 else {
                            "energy-kcal_100g": 400, "proteins_100g": 5,
                            "carbohydrates_100g": 50, "fat_100g": 18})
                for i in range(10)
            ]

    outcome = hunt.sweep(MostlyIncomplete(), ["tol"], limit=10,
                         out=tmp_path / "o", work_dir=tmp_path / "w")[0]
    assert not outcome.errors and not outcome.warns
    assert [r.name for r in outcome.tolerated] == ["completeness"]
    assert outcome.findings == 1, "a tolerated rule must still rank the term"


def test_a_report_is_written_per_term(tmp_path: Path) -> None:
    out = tmp_path / "out"
    hunt.sweep(StubClient(), ["clean", "dirty"], limit=5,
               out=out, work_dir=tmp_path / "work")
    assert (out / "clean" / "report.md").exists()
    dirty = (out / "dirty" / "report.md").read_text(encoding="utf-8")
    assert "implausible_energy" in dirty
    assert "## Findings" in dirty, "the offending rows must reach the file"


def test_league_table_puts_the_worst_slice_first(swept: list[hunt.Outcome]) -> None:
    table = hunt.league_table(swept)
    assert table.index("dirty") < table.index("clean")
    assert "unreachable" in table
    assert "Start with `dirty`" in table


@pytest.mark.parametrize(
    ("term", "expected"),
    [("aceite", "aceite"), ("suplemento proteina", "suplemento-proteina"),
     ("Salsa  Picante!", "salsa-picante"), ("///", "term")],
)
def test_slug_is_filesystem_safe(term: str, expected: str) -> None:
    assert hunt.slug(term) == expected


def test_consolidated_groups_by_rule_not_by_term(swept: list[hunt.Outcome]) -> None:
    """The same rule firing in six slices is one finding with six examples.

    Grouped by term it reads as six problems and invites six write-ups.
    """
    text = hunt.consolidated(swept)
    assert text.count("## `implausible_energy`") == 1
    assert "**dirty**" in text
    assert "upstream-down" in text, "an unswept term must be declared, not omitted"


def test_consolidated_puts_errors_above_warnings(swept: list[hunt.Outcome]) -> None:
    text = hunt.consolidated(swept)
    assert text.index("implausible_energy") < text.index("energy_macro_consistency")


def test_consolidated_says_so_when_nothing_fired(tmp_path: Path) -> None:
    class Clean:
        def search(self, term: str, page_size: int = 50) -> list[dict[str, Any]]:
            return [payload(f"6{i:012d}") for i in range(5)]

    outcomes = hunt.sweep(Clean(), ["ok"], limit=5,
                          out=tmp_path / "o", work_dir=tmp_path / "w")
    assert "Nothing fired in any slice." in hunt.consolidated(outcomes)
