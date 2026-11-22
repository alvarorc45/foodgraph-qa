#!/usr/bin/env python3
"""Record real API responses into tests/fixtures/recorded_sample.json.

This does NOT touch `products.json`. The two fixture files answer different
questions and must not be mixed:

  products.json        curated, clean by construction. Answers "does a good
                       load pass every check?". If real-world data went in
                       here, the suite would go red whenever Open Food Facts
                       gained a bad record — which says nothing about this code.

  recorded_sample.json real payloads, warts and all. Answers "does the parser
                       survive what the API actually sends?". Optional: the
                       suite skips its tests when the file is absent.

    python scripts/record_fixtures.py --search chocolate -n 10
    python scripts/record_fixtures.py --barcodes 3017620422003 ...
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from foodgraph.client import OffClient, OffError  # noqa: E402
from foodgraph.ingest import normalise  # noqa: E402

TARGET = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "recorded_sample.json"


def _record_barcodes(client: OffClient, codes: list[str]) -> list[dict]:
    payloads = []
    for code in codes:
        product = client.product(code)
        if product is None:
            # Loudly, because a silently skipped barcode is how you end up
            # writing a one-product fixture and wondering why tests broke.
            print(f"  NOT FOUND {code} — skipped")
            continue
        payloads.append(product)
        print(f"  recorded {code}: {product.get('product_name') or '(no name)'}")
    return payloads


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--search", metavar="TERM",
                        help="record whatever the API returns for TERM")
    source.add_argument("--barcodes", nargs="+", metavar="CODE",
                        help="record these specific barcodes")
    parser.add_argument("-n", "--limit", type=int, default=10)
    args = parser.parse_args()

    client = OffClient()

    # An upstream outage is not a crash in this script, so it does not get to
    # look like one. `cgi/search.pl` is the legacy Open Food Facts endpoint and
    # returns 503 under load often enough that a stack trace here would train
    # whoever is reading it to distrust real tracebacks.
    try:
        if args.search:
            payloads = client.search(args.search, page_size=args.limit)
            print(f"Search for {args.search!r} returned {len(payloads)} products")
        else:
            payloads = _record_barcodes(client, args.barcodes)
    except OffError as exc:
        print(f"Could not reach the API: {exc}")
        print("The client already retried with backoff. A 5xx is upstream "
              "having a bad day — try again in a minute. A connection error is "
              "local. Either way the existing fixtures are untouched.")
        return 2

    if not payloads:
        print("\nNothing recorded. Leaving the existing file alone.")
        return 1

    unparseable = [p.get("code") for p in payloads if normalise(p) is None]
    if unparseable:
        print(f"\nNote: {len(unparseable)} payload(s) the parser rejects: {unparseable}")
        print("That is a finding, not a bug in this script — worth a bug report.")

    TARGET.write_text(
        json.dumps(payloads, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"\nWrote {len(payloads)} products to {TARGET.relative_to(Path.cwd())}")
    print("products.json was not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
