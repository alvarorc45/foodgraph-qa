"""Open Food Facts v2 client.

Thin on purpose. One session, explicit timeouts, bounded retries, and a
request rate that won't get us blocked - OFF is volunteer-run and asks
callers to identify themselves.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://world.openfoodfacts.org"
SEARCH_URL = "https://search.openfoodfacts.org"

# Requesting an explicit field list keeps responses small, and doubles as the
# contract schema_drift compares against.
PRODUCT_FIELDS = (
    "code",
    "product_name",
    "brands",
    "brands_tags",
    "categories_tags",
    "ingredients_tags",
    "additives_tags",
    "quantity",
    "nutriments",
    "last_modified_t",
)

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


class OffError(RuntimeError):
    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class OffClient:
    user_agent: str = "foodgraph-qa/0.1 (https://github.com/alvarorc45/foodgraph-qa)"
    base_url: str = BASE_URL
    timeout: float = 15.0
    max_attempts: int = 3
    # Min seconds between requests. Cheaper than handling the 429.
    min_interval: float = 0.4
    session: requests.Session = field(default_factory=requests.Session, repr=False)
    _last_request: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.session.headers.update({"User-Agent": self.user_agent})

    def product(self, barcode: str) -> dict[str, Any] | None:
        """One product, or None if the barcode isn't in the database.

        Not-found is an answer, not a failure, hence None rather than an
        exception. Watch out for the two spellings - OFF returns 404 for some
        missing products and a 200 carrying `status: 0` for others. Check only
        the status code and the second kind quietly becomes an empty product.
        """
        url = f"{self.base_url}/api/v2/product/{barcode}"
        params = {"fields": ",".join(PRODUCT_FIELDS)}
        response = self._get(url, params=params, allow_404=True)
        if response is None:
            return None

        body = response.json()
        if body.get("status") == 0 or body.get("status_verbose") == "product not found":
            return None
        product = body.get("product")
        return product if isinstance(product, dict) else None

    def search(self, query: str, page_size: int = 20) -> list[dict[str, Any]]:
        url = f"{self.base_url}/cgi/search.pl"
        params = {
            "search_terms": query,
            "search_simple": "1",
            "action": "process",
            "json": "1",
            "page_size": str(page_size),
            "fields": ",".join(PRODUCT_FIELDS),
        }
        response = self._get(url, params=params)
        assert response is not None  # allow_404 is False, so never None here
        products = response.json().get("products", [])
        return [p for p in products if isinstance(p, dict)]

    def _get(
        self,
        url: str,
        *,
        params: dict[str, str] | None = None,
        allow_404: bool = False,
    ) -> requests.Response | None:
        last_error: str = "no attempt made"

        for attempt in range(1, self.max_attempts + 1):
            self._wait_turn()
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.Timeout:
                last_error = f"timed out after {self.timeout}s"
                log.warning("GET %s attempt %d: %s", url, attempt, last_error)
                continue
            except requests.RequestException as exc:
                # Don't retry connection-level failures - they don't fix
                # themselves in a second and we'd just stall for three.
                raise OffError(f"request to {url} failed: {exc}") from exc

            if response.status_code == 404 and allow_404:
                return None
            if response.ok:
                return response
            if response.status_code in RETRYABLE_STATUS and attempt < self.max_attempts:
                delay = self._backoff(attempt, response)
                log.warning(
                    "GET %s attempt %d: HTTP %d, retrying in %.1fs",
                    url, attempt, response.status_code, delay,
                )
                time.sleep(delay)
                last_error = f"HTTP {response.status_code}"
                continue

            raise OffError(
                f"GET {url} returned HTTP {response.status_code}",
                status=response.status_code,
            )

        raise OffError(f"GET {url} failed after {self.max_attempts} attempts: {last_error}")

    def _backoff(self, attempt: int, response: requests.Response) -> float:
        header = response.headers.get("Retry-After")
        if header:
            try:
                return min(float(header), 30.0)
            except ValueError:
                pass
        return min(2.0 ** (attempt - 1), 8.0)

    def _wait_turn(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request = time.monotonic()
