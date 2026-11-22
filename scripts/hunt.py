#!/usr/bin/env python3
"""Sweep several slices of the dataset and report where the defects are.

`run_checks.py` validates one pull and gates on it. This does the other job,
surveying enough of the dataset to find out *where* the bad data lives.

Worth keeping separate. Popular products are the best-curated ones, so a
sweep of "chocolate" comes back clean and proves nothing except that the
community looks after its flagship records. The defects sit in the quiet
corners - niche categories, local brands, nutrition typed in once in 2018
and never revisited.

    python scripts/hunt.py
    python scripts/hunt.py --terms aceite "especias" "suplemento proteina"
    python scripts/hunt.py -n 100 --out reports/hunt

Always exits 0. A survey that fails because it found what it was looking for
would be an odd thing to put in a pipeline; use run_checks.py to gate.
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from foodgraph import ingest, report  # noqa: E402
from foodgraph.checks.base import CheckResult, Severity  # noqa: E402
from foodgraph.client import OffClient, OffError  # noqa: E402

# Starting points, not a definitive list. Weighted towards categories where
# portion sizes and hand-typed labels tend to go wrong - supplements declare
# energy per scoop, oils sit right at the physical limit of what food can
# contain.
DEFAULT_TERMS = (
    "aceite",
    "especias",
    "suplemento proteina",
    "levadura",
    "infusion",
    "salsa picante",
    "pan sin gluten",
    "turron",
)


class SearchClient(Protocol):
    """Only the method the sweep needs, so tests can drive it with a stub
    instead of the live API."""

    def search(self, term: str, page_size: int = ...) -> list[dict]: ...


def slug(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.lower()).strip("-") or "term"


class Outcome:
    __slots__ = ("term", "loaded", "requested", "results", "error")

    def __init__(
        self,
        term: str,
        loaded: int = 0,
        requested: int = 0,
        results: list[CheckResult] | None = None,
        error: str | None = None,
    ) -> None:
        self.term = term
        self.loaded = loaded
        self.requested = requested
        self.results = results or []
        self.error = error

    @property
    def errors(self) -> list[CheckResult]:
        return [r for r in self.results
                if not r.passed and r.severity is Severity.ERROR]

    @property
    def warns(self) -> list[CheckResult]:
        return [r for r in self.results
                if not r.passed and r.severity is Severity.WARN]

    @property
    def tolerated(self) -> list[CheckResult]:
        return [r for r in self.results if r.tolerated]

    @property
    def findings(self) -> int:
        # Tolerated counts. A rule that found eight broken rows and let them
        # through has still found eight broken rows.
        return len(self.errors) + len(self.warns) + len(self.tolerated)


def sweep(
    client: SearchClient,
    terms: list[str],
    *,
    limit: int,
    out: Path,
    work_dir: Path,
) -> list[Outcome]:
    # Make our own directories instead of trusting the caller. A sweep that
    # only works when called from main() is one the tests can't drive.
    work_dir.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)

    outcomes = []
    for term in terms:
        print(f"\n--- {term} ---")
        try:
            payloads = client.search(term, page_size=limit)
        except OffError as exc:
            # One bad slice mustn't end the sweep - 5xx from this endpoint is
            # common enough that aborting means never finishing a run.
            print(f"  skipped: {exc}")
            outcomes.append(Outcome(term, error=str(exc)))
            continue

        db_path = work_dir / f"{slug(term)}.db"
        db_path.unlink(missing_ok=True)
        conn = ingest.connect(db_path)
        counts = ingest.load(conn, payloads, source=f"search:{term}")
        results = report.run_all(conn)
        conn.close()

        term_out = out / slug(term)
        term_out.mkdir(parents=True, exist_ok=True)
        (term_out / "report.md").write_text(
            report.to_markdown(results, title=f"Data quality report — {term}"),
            encoding="utf-8",
        )

        outcome = Outcome(term, counts["loaded"], counts["requested"], results)
        outcomes.append(outcome)
        print(f"  {outcome.loaded}/{outcome.requested} loaded, "
              f"{len(outcome.errors)} errors, {len(outcome.warns)} warnings, "
              f"{len(outcome.tolerated)} tolerated")
    return outcomes


def league_table(outcomes: list[Outcome]) -> str:
    lines = [
        "",
        "=" * 72,
        f"{'term':<24} {'loaded':>7} {'err':>5} {'warn':>5} {'allow':>6}  rules that fired",
        "-" * 72,
    ]
    for outcome in sorted(outcomes, key=lambda o: o.findings, reverse=True):
        if outcome.error:
            lines.append(f"{outcome.term:<24} {'—':>7} {'—':>5} {'—':>5} {'—':>6}  unreachable")
            continue
        fired = ", ".join(
            r.name for r in outcome.errors + outcome.warns + outcome.tolerated
        ) or "nothing"
        lines.append(
            f"{outcome.term:<24} {outcome.loaded:>7} {len(outcome.errors):>5} "
            f"{len(outcome.warns):>5} {len(outcome.tolerated):>6}  {fired}"
        )
    lines.append("=" * 72)

    best = [o for o in sorted(outcomes, key=lambda o: o.findings, reverse=True)
            if o.findings]
    if best:
        lines += [
            "",
            f"Start with `{best[0].term}`: {best[0].findings} rules fired. The "
            "offending rows are in that term's report.md, under Findings and "
            "Tolerated.",
        ]
    else:
        lines += [
            "",
            "Nothing fired anywhere. That is a result about the slices, not "
            "about the harness: try narrower or more obscure terms, or raise -n.",
        ]
    return "\n".join(lines)


# Rows per rule per term in the consolidated file. The per-term reports keep
# the full set; this one exists to be read in one sitting.
CONSOLIDATED_SAMPLES = 3


def consolidated(outcomes: list[Outcome]) -> str:
    """Every rule that fired anywhere, grouped by rule rather than by term.

    The per-term reports tell you how healthy one slice is. This tells you
    what's wrong with the dataset and how often, which is the question you
    actually have once a sweep finishes. The same rule firing in six slices
    is one finding with six examples, and only this view shows that.
    """
    by_rule: dict[str, list[tuple[str, CheckResult]]] = {}
    for outcome in outcomes:
        for result in outcome.errors + outcome.warns + outcome.tolerated:
            by_rule.setdefault(result.name, []).append((outcome.term, result))

    unreachable = [o.term for o in outcomes if o.error]
    lines = ["# Findings across the sweep", ""]
    if unreachable:
        lines += [f"_Not swept (upstream unreachable): {', '.join(unreachable)}_", ""]
    if not by_rule:
        lines += ["Nothing fired in any slice."]
        return "\n".join(lines) + "\n"

    def rank(item: tuple[str, list[tuple[str, CheckResult]]]) -> tuple[int, int]:
        _, hits = item
        worst = 0 if any(not r.passed and r.severity is Severity.ERROR
                         for _, r in hits) else 1 if any(not r.passed for _, r in hits) else 2
        return worst, -sum(r.offenders for _, r in hits)

    for rule, hits in sorted(by_rule.items(), key=rank):
        total = sum(r.offenders for _, r in hits)
        lines += [
            f"## `{rule}`",
            "",
            hits[0][1].description,
            "",
            f"Fired in {len(hits)} slice(s), {total} offending row(s) in total.",
            "",
        ]
        for term, result in hits:
            state = ("tolerated" if result.tolerated
                     else result.severity.value.upper())
            lines += [f"**{term}** — {result.offenders}/{result.examined} ({state})", ""]
            if not result.samples:
                lines += ["_no rows reported_", ""]
                continue
            keys = list(result.samples[0].keys())
            lines.append("| " + " | ".join(keys) + " |")
            lines.append("| " + " | ".join("---" for _ in keys) + " |")
            for sample in result.samples[:CONSOLIDATED_SAMPLES]:
                lines.append("| " + " | ".join(
                    str(sample.get(k, "")).replace("|", "\\|") for k in keys) + " |")
            lines.append("")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--terms", nargs="+", default=list(DEFAULT_TERMS))
    parser.add_argument("-n", "--limit", type=int, default=100)
    parser.add_argument("--out", default="reports/hunt")
    parser.add_argument("--work-dir", default=".hunt")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    out = Path(args.out)
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    outcomes = sweep(OffClient(), args.terms, limit=args.limit, out=out, work_dir=work_dir)
    print(league_table(outcomes))

    findings = out / "FINDINGS.md"
    findings.write_text(consolidated(outcomes), encoding="utf-8")
    print(f"\nEverything that fired, grouped by rule: {findings}")
    print(f"Per-term detail under {out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
