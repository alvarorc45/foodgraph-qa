"""One registry for every check.

Lets pytest parametrise over all the rules and the report render all of
them without either keeping its own list that falls out of date.
"""

from __future__ import annotations

from .base import Check, CheckResult, Severity
from .graph import CHECKS as GRAPH_CHECKS
from .quality import CHECKS as QUALITY_CHECKS

ALL_CHECKS: tuple[Check, ...] = GRAPH_CHECKS + QUALITY_CHECKS

__all__ = [
    "Check", "CheckResult", "Severity",
    "GRAPH_CHECKS", "QUALITY_CHECKS", "ALL_CHECKS",
]


def by_name(name: str) -> Check:
    for check in ALL_CHECKS:
        if check.name == name:
            return check
    raise KeyError(f"no check named {name!r}")
