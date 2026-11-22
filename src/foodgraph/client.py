"""Client for the Open Food Facts REST API (v2).

Deliberately thin: one session, explicit timeouts, bounded retries and a
polite request rate. Open Food Facts is a volunteer-run service that asks
callers to identify themselves and not to hammer it, so the User-Agent is
mandatory here rather than optional.
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

#: Asking for a field list keeps responses small and, more importantly, makes
#: the contract explicit: these are the fields the pipeline depends on, so the
#: schema-drift check has something concrete to compare against.
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
    """A request failed in a way the caller cannot paper over."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class OffClient:
    user_agent: str = "foodgraph-qa/0.1 (https://github.com/your-user/foodgraph-qa)"
    base_url: str = BASE_URL
    timeout: float = 15.0
    max_attempts: int = 3
    #: Minimum seconds between requests. Open Food Facts rate-limits the
    #: product endpoint; staying under it is cheaper than handling the 429.
    min_interval: float = 0.4
    session: requests.Session = field(default_factory=requests.Session, repr=False)
    _last_request: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        self.session.headers.update({"User-Agent": self.user_agent})

    # ------------------------------------------------------------------ public

    def product(self, barcode: str) -> dict[str, Any] | None:
        """Return one product, or None when the barcode is not in the database.

        "Not found" is a legitimate answer, not a failure, so it is None rather
        than an exception. The API signals it with ``status: 0`` in a 200
        response as well as with a 404, and both spellings are handled: relying
        on the status code alone silently turns missing products into empty
        ones, which is how a pipeline ends up loading blank rows.
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
        """Full-text search. Used to build a working set of products."""
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
        assert response is not None  # allow_404 is False, so this cannot be None
        products = response.json().get("products", [])
        return [p for p in products if isinstance(p, dict)]

    # ----------------------------------------------------------------- private

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
                # Connection-level failures are not worth retrying three times;
                # they almost never fix themselves within a second.
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
        """Exponential backoff, but obey Retry-After when the server sends it."""
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
