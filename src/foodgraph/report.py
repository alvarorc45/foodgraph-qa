"""Render check results.

The same results that pytest asserts on are what gets rendered here. That is
the point of the single `CheckResult` shape: the monitoring and the test suite
cannot disagree about whether the data is healthy, because they are reading
the same rule.
"""

from __future__ import annotations

import html
import sqlite3
from datetime import datetime, timezone
from typing import Iterable

from .checks import ALL_CHECKS, Check
from .checks.base import CheckResult, Severity


def run_all(conn: sqlite3.Connection, checks: Iterable[Check] = ALL_CHECKS) -> list[CheckResult]:
    results = []
    for check in checks:
        try:
            results.append(check.run(conn))
        except sqlite3.Error as exc:
            # A check that crashes is a failure, not an absence. Swallowing it
            # would turn a broken validator into a silent green tick, which is
            # the worst outcome a monitoring system can produce.
            results.append(
                CheckResult(
                    name=check.name,
                    severity=Severity.ERROR,
                    passed=False,
                    offenders=1,
                    examined=0,
                    description=f"the check itself failed to run: {exc}",
                )
            )
    return results


def exit_code(results: list[CheckResult]) -> int:
    """Non-zero when an ERROR-severity check failed.

    Warnings deliberately do not break the build. On crowd-sourced data there
    is always a tail of implausible records, and a pipeline that goes red
    every morning for something nobody can fix is a pipeline whose alerts get
    ignored within a week.
    """
    return 1 if any(
        not r.passed and r.severity is Severity.ERROR for r in results
    ) else 0


def _state(result: CheckResult) -> str:
    """The word a reader sees. `allow` is not the same as `pass`.

    A check that found eight broken rows and shrugged has not passed in any
    sense a reader would recognise, so it does not get to borrow the word.
    """
    if not result.passed:
        return result.severity.value
    return "allow" if result.tolerated else "pass"


def to_markdown(results: list[CheckResult], *, title: str = "Data quality report") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    failed_errors = [r for r in results if not r.passed and r.severity is Severity.ERROR]
    failed_warns = [r for r in results if not r.passed and r.severity is Severity.WARN]
    tolerated = [r for r in results if r.tolerated]
    clean = [r for r in results if r.passed and not r.tolerated]

    lines = [
        f"# {title}",
        "",
        f"_{now}_",
        "",
        f"**{len(clean)} clean · {len(tolerated)} tolerated · "
        f"{len(failed_warns)} warnings · {len(failed_errors)} errors**",
        "",
        "| Check | Severity | Result | Offenders | Examined |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for result in results:
        state = _state(result)
        lines.append(
            f"| `{result.name}` | {result.severity.value} | {state} | "
            f"{result.offenders} | {result.examined} |"
        )

    problems = failed_errors + failed_warns
    if problems:
        lines += ["", "## Findings", ""]
        for result in problems:
            lines += [
                f"### `{result.name}` — {result.severity.value}",
                "",
                result.description,
                "",
                f"{result.offenders} of {result.examined} rows ({result.ratio:.1%}).",
                "",
            ]
            if result.samples:
                keys = list(result.samples[0].keys())
                lines.append("| " + " | ".join(keys) + " |")
                lines.append("| " + " | ".join("---" for _ in keys) + " |")
                for sample in result.samples:
                    cells = [str(sample.get(k, "")) for k in keys]
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")

    if tolerated:
        lines += [
            "",
            "## Tolerated",
            "",
            "Rules that found broken rows but not enough of them to fail. These "
            "did not break the build and they are not noise either: the rows "
            "below are real. A report that folded them in with the clean "
            "results would be hiding its own findings.",
            "",
        ]
        for result in tolerated:
            lines += [
                f"### `{result.name}`",
                "",
                result.description,
                "",
                f"{result.offenders} of {result.examined} "
                f"({result.ratio:.1%}), tolerance {result.tolerance:.0%}.",
                "",
            ]
            if result.samples:
                keys = list(result.samples[0].keys())
                lines.append("| " + " | ".join(keys) + " |")
                lines.append("| " + " | ".join("---" for _ in keys) + " |")
                for sample in result.samples:
                    cells = [str(sample.get(k, "")) for k in keys]
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")

    return "\n".join(lines) + "\n"


def to_html(results: list[CheckResult], *, title: str = "Data quality report") -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    colours = {
        "pass": "#1a7f37",
        "allow": "#57606a",
        "warn": "#9a6700",
        "error": "#cf222e",
    }

    rows = []
    for result in results:
        state = _state(result)
        detail = ""
        if result.tolerated:
            detail = (
                f' <span style="color:#57606a">(within {result.tolerance:.0%})</span>'
            )
        rows.append(
            f'<tr><td><code>{html.escape(result.name)}</code></td>'
            f'<td style="color:{colours[state]};font-weight:600">{state}{detail}</td>'
            f'<td style="text-align:right">{result.offenders}</td>'
            f'<td style="text-align:right">{result.examined}</td>'
            f'<td>{html.escape(result.description)}</td></tr>'
        )

    failed = sum(1 for r in results if not r.passed)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
  body {{ font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 60rem; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border-bottom: 1px solid #d0d7de; padding: .5rem .6rem; text-align: left; }}
  th {{ background: #f6f8fa; }}
  code {{ background: #f6f8fa; padding: .1rem .3rem; border-radius: 4px; }}
</style></head>
<body>
<h1>{html.escape(title)}</h1>
<p>{now} — {sum(1 for r in results if r.passed and not r.tolerated)} clean,
{sum(1 for r in results if r.tolerated)} tolerated, {failed} failed</p>
<table>
<thead><tr><th>Check</th><th>Result</th><th>Offenders</th><th>Examined</th><th>Rule</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
</table>
</body></html>
"""


def to_console(results: list[CheckResult]) -> str:
    return "\n".join(result.summary() for result in results)
