"""Contract tests against the live Open Food Facts API.

Marked `live` and excluded from the default run: they need the network, so
they can fail for reasons that have nothing to do with this code. They answer
a question the offline suite cannot — "does the real API still behave the way
the pipeline assumes?" — and CI runs them on a schedule, where a failure means
upstream changed rather than that someone broke the build.

    pytest -m live
"""

from __future__ import annotations

import pytest

from foodgraph.client import PRODUCT_FIELDS, OffClient
from foodgraph.ingest import normalise

pytestmark = pytest.mark.live

# Nutella. One of the most-edited records in the database, so it is about as
# stable a fixture as a crowd-sourced dataset offers.
KNOWN_BARCODE = "3017620422003"


def test_known_product_is_returned(off_client: OffClient) -> None:
    product = off_client.product(KNOWN_BARCODE)
    assert product is not None
    assert product["code"] == KNOWN_BARCODE


def test_unknown_barcode_returns_none_not_an_empty_product(off_client: OffClient) -> None:
    """The failure mode this guards against is loading blank rows.

    The API answers an unknown barcode with HTTP 200 and `status: 0`, so a
    client that only inspects the status code gets a product-shaped object
    with nothing in it and happily writes it to the database.
    """
    assert off_client.product("0000000000000") is None


def test_response_still_carries_every_requested_field(off_client: OffClient) -> None:
    """Schema drift, caught at the source instead of three steps downstream."""
    product = off_client.product(KNOWN_BARCODE)
    assert product is not None
    missing = [
        field for field in PRODUCT_FIELDS
        if field not in product and field != "code"
    ]
    assert not missing, f"the API no longer returns: {missing}"


def test_live_payload_still_normalises(off_client: OffClient) -> None:
    """The real payload must survive the parser, not just the recorded one."""
    product = off_client.product(KNOWN_BARCODE)
    assert product is not None
    record = normalise(product)
    assert record is not None
    assert record.code == KNOWN_BARCODE
    assert record.edges, "a well-known product should produce relationships"


def test_search_returns_usable_products(off_client: OffClient) -> None:
    products = off_client.search("chocolate", page_size=5)
    assert products
    assert all("code" in p for p in products)
