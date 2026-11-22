#!/usr/bin/env python3
"""Ingest, validate, report. The entry point CI and a human both use.

    python scripts/run_checks.py --from-fixtures          # offline, deterministic
    python scripts/run_checks.py --search chocolate -n 50 # live pull

Exits non-zero when an ERROR-severity check fails, so it can gate a pipeline.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from foodgraph import ingest, report  # noqa: E402
from foodgraph.client import OffClient  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "products.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--from-fixtures", action="store_true",
                        help="load recorded payloads instead of calling the API")
    source.add_argument("--search", metavar="TERM",
                        help="pull products matching TERM from the live API")
    source.add_argument("--barcodes", nargs="+", metavar="CODE",
                        help="pull specific barcodes from the live API")
    parser.add_argument("-n", "--limit", type=int, default=50,
                        help="how many products to pull when searching")
    parser.add_argument("--db", default="graph.db", help="where to write the database")
    parser.add_argument("--out", default="reports", help="directory for the report")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    db_path = Path(args.db)
    if db_path.exists():
        db_path.unlink()
    conn = ingest.connect(db_path)

    if args.from_fixtures:
        payloads = json.loads(FIXTURES.read_text(encoding="utf-8"))
        origin = "fixtures"
    else:
        client = OffClient()
        if args.search:
            payloads = client.search(args.search, page_size=args.limit)
            origin = f"search:{args.search}"
        else:
            fetched = (client.product(code) for code in args.barcodes)
            payloads = [p for p in fetched if p is not None]
            origin = "barcodes"

    counts = ingest.load(conn, payloads, source=origin)
    print(f"Loaded {counts['loaded']}/{counts['requested']} products "
          f"({counts['failed']} unusable) from {origin}\n")

    results = report.run_all(conn)
    print(report.to_console(results))

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(report.to_markdown(results), encoding="utf-8")
    (out / "report.html").write_text(report.to_html(results), encoding="utf-8")
    print(f"\nReport written to {out / 'report.md'} and {out / 'report.html'}")

    conn.close()
    return report.exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
