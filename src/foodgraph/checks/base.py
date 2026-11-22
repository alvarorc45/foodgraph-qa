"""Check abstraction.

Every rule is a `Check` returning a `CheckResult`. One shape for all of them
so the same definitions drive pytest, the report and the CI exit code. If the
tests and the monitoring kept separate definitions they'd drift, and the
monitoring is the one that would stop being trusted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Severity(str, Enum):
    # Breaks a guarantee the pipeline is meant to provide. Fails the build.
    ERROR = "error"
    # Worth a look, not proof of a bug. Crowd-sourced data always has a tail
    # of these and failing on them teaches people to ignore the alerts.
    WARN = "warn"


@dataclass(slots=True)
class CheckResult:
    name: str
    severity: Severity
    passed: bool
    offenders: int
    # How many rows were looked at. "3 of 5" and "3 of 50000" are different
    # stories and the denominator is what tells them apart.
    examined: int
    description: str
    # A few offending rows, so a failure is actionable without re-running the
    # query by hand.
    samples: list[dict[str, Any]] = field(default_factory=list)
    tolerance: float = 0.0

    @property
    def ratio(self) -> float:
        return self.offenders / self.examined if self.examined else 0.0

    @property
    def tolerated(self) -> bool:
        """Found broken rows, but too few to fail on.

        Has to be visible. Printing `0/1841` and `8/50` as the same green PASS
        throws away the only interesting part, which is that the second one
        found real rows and decided to live with them.
        """
        return self.passed and self.offenders > 0

    def summary(self) -> str:
        if not self.passed:
            state = self.severity.value.upper()
        elif self.tolerated:
            state = "ALLOW"
        else:
            state = "PASS"
        line = f"[{state}] {self.name}: {self.offenders}/{self.examined} — {self.description}"
        if self.tolerated:
            line += f" (tolerated: {self.ratio:.0%} ≤ {self.tolerance:.0%})"
        return line


@dataclass(slots=True)
class Check:
    name: str
    severity: Severity
    description: str
    run: Callable[[sqlite3.Connection], CheckResult]
    tags: tuple[str, ...] = ()


MAX_SAMPLES = 5


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows[:MAX_SAMPLES]]


def sql_check(
    conn: sqlite3.Connection,
    *,
    name: str,
    severity: Severity,
    description: str,
    offenders_sql: str,
    examined_sql: str,
    params: tuple[Any, ...] = (),
    tolerance: float = 0.0,
) -> CheckResult:
    """Run a rule written as "this query returns the rows that are wrong".

    `tolerance` is the share of bad rows allowed before the rule fails. Zero
    is right for structural rules - a broken edge is always a bug - and wrong
    for statistical ones, where demanding that 100% of crowd-sourced products
    declare their energy would just fail forever.
    """
    offending_rows = conn.execute(offenders_sql, params).fetchall()
    examined = conn.execute(examined_sql).fetchone()[0]
    offenders = len(offending_rows)
    ratio = offenders / examined if examined else 0.0
    return CheckResult(
        name=name,
        severity=severity,
        passed=ratio <= tolerance,
        offenders=offenders,
        examined=examined,
        description=description,
        samples=rows_to_dicts(offending_rows),
        tolerance=tolerance,
    )
