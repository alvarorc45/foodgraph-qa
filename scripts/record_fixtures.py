#!/usr/bin/env python3
"""Record real API responses into tests/fixtures/recorded_sample.json.

Does NOT touch products.json. The two files answer different questions and
mixing them breaks both.

products.json is curated and clean by construction, and answers whether a
good load passes every check. Put real-world data in there and the suite
goes red whenever OFF gains a bad record, which says nothing about this code.

recorded_sample.json is real payloads, warts and all, and answers whether the
parser survives what the API actually sends. Optional - its tests skip when
the file isn't there.

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
            # Loudly. A silently skipped barcode is how you end up with a
            # one-product fixture wondering why half the suite broke.
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

    # An upstream outage isn't a crash in this script and shouldn't look
    # like one. cgi/search.pl is the legacy endpoint and 503s under load
    # often enough that a stack trace here teaches people to ignore real
    # tracebacks.
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
