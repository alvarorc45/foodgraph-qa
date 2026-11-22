"""The check abstraction.

Every validation is a `Check` that returns a `CheckResult`. One shape for all
of them is what lets the same definitions drive three consumers at once: the
pytest suite asserts on them, the report renders them, and CI fails the build
on them. Writing the rules once and reusing them is the whole point; if the
tests and the monitoring drifted apart, the monitoring would be the one that
stops being trusted.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable


class Severity(str, Enum):
    #: Breaks a guarantee the pipeline is supposed to provide. Fails the build.
    ERROR = "error"
    #: Suspicious and worth a human look, but not proof of a defect. Reported,
    #: does not fail the build. Crowd-sourced data always has a tail of these,
    #: and treating them as errors would train everyone to ignore the alerts.
    WARN = "warn"


@dataclass(slots=True)
class CheckResult:
    name: str
    severity: Severity
    passed: bool
    #: How many rows violated the rule.
    offenders: int
    #: How many rows the rule looked at, for context ("3 of 5" vs "3 of 50000").
    examined: int
    description: str
    #: A handful of offending rows, so a failure is actionable without
    #: re-running the query by hand. This is the difference between an alert
    #: someone can fix and one they learn to mute.
    samples: list[dict[str, Any]] = field(default_factory=list)
    #: The share of offending rows this rule was willing to absorb.
    tolerance: float = 0.0

    @property
    def ratio(self) -> float:
        return self.offenders / self.examined if self.examined else 0.0

    @property
    def tolerated(self) -> bool:
        """Offending rows were found, but too few to fail the rule.

        This state has to be visible. A report that prints `0/1841` and
        `8/50` as the same green PASS has thrown away the only information
        worth having: the second one found real broken rows and decided to
        live with them. Whoever reads the report should be told that, not
        have to go and re-run the query to discover it.
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
    """Run a rule expressed as "this query returns the rows that are wrong".

    `tolerance` is the share of offending rows allowed before the check fails.
    It exists because a threshold of exactly zero is the right call for
    structural rules (a broken edge is always a bug) and the wrong call for
    statistical ones (demanding that 100% of crowd-sourced products declare
    their energy would fail forever and tell nobody anything).
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
