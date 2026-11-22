"""Data-quality checks: completeness, volume, freshness and anomalies.

Where the graph module asks "is this well formed?", this one asks "is this
believable?". The two fail for different reasons and get different severities:
a broken edge is a pipeline bug, an implausible calorie figure is usually a
contributor typo upstream. Both matter to whoever consumes the data, but only
one of them is the pipeline's fault.
"""

from __future__ import annotations

import json
import sqlite3
import time

from typing import Any

from .base import MAX_SAMPLES, Check, CheckResult, Severity, rows_to_dicts, sql_check

# Physical bounds. Fat is the densest macronutrient at ~9 kcal/g, so nothing
# edible exceeds ~900 kcal per 100 g; pure oil sits just under it.
MAX_KCAL_100G = 900.0
#: Macros are grams per 100 g, so they cannot sum past 100. A small margin
#: absorbs honest rounding in the source data rather than flagging it.
MACRO_SUM_LIMIT = 100.0 + 2.0
#: Atwater factors, for cross-checking declared energy against declared macros.
KCAL_PER_G = {"protein": 4.0, "carbs": 4.0, "fat": 9.0}
#: How far declared energy may stray from computed energy before it is
#: suspicious. Fibre, polyols and rounding all move the number legitimately,
#: so the band is wide: this is meant to catch unit mix-ups and typos, not to
#: police nutrition labelling.
ENERGY_TOLERANCE = 0.30
#: Products older than this are stale rather than merely old.
STALE_AFTER_DAYS = 365 * 3
#: A requested field counts as dropped upstream once it is missing from this
#: share of payloads. Set high on purpose: the API omits a field when it is
#: empty for a product, so scattered absences are empty values, while a real
#: schema change takes the field out of everything at once. See `schema_drift`.
FIELD_ABSENT_SHARE = 0.90


def completeness(conn: sqlite3.Connection) -> CheckResult:
    """Products missing any field the downstream consumer actually needs.

    "Complete" is defined by what breaks if it is absent, not by what the API
    could return. A calorie tracker cannot do anything with a product that has
    no name or no energy, so those two are the bar.
    """
    return sql_check(
        conn,
        name="completeness",
        severity=Severity.WARN,
        description="products should have a name and an energy value",
        offenders_sql="""
            SELECT code, name, energy_kcal_100g
            FROM product
            WHERE name IS NULL OR TRIM(name) = '' OR energy_kcal_100g IS NULL
        """,
        examined_sql="SELECT COUNT(*) FROM product",
        tolerance=0.20,
    )


def volume(conn: sqlite3.Connection) -> CheckResult:
    """Did the last run load roughly what it asked for?

    Catches the partial load: the run that fetched 500 products, hit an error
    at 40 and committed anyway. Nothing downstream errors, the numbers just
    quietly get smaller.
    """
    row = conn.execute(
        """
        SELECT id, requested, loaded, failed
        FROM ingest_run
        WHERE finished_at IS NOT NULL
        ORDER BY id DESC LIMIT 1
        """
    ).fetchone()

    if row is None:
        return CheckResult(
            name="volume", severity=Severity.ERROR, passed=False,
            offenders=1, examined=1,
            description="no completed ingest run found",
            samples=[],
        )

    requested = row["requested"] or 0
    loaded = row["loaded"] or 0
    shortfall = requested - loaded
    # A tenth of the batch failing is a bad run, not a bad record.
    passed = requested > 0 and shortfall <= max(1, int(requested * 0.10))
    return CheckResult(
        name="volume",
        severity=Severity.ERROR,
        passed=passed,
        offenders=shortfall,
        examined=requested,
        description="the latest run should load nearly everything it requested",
        samples=[dict(row)],
    )


def freshness(conn: sqlite3.Connection) -> CheckResult:
    """Products nobody has touched upstream in years.

    Freshness here is about the source data, not about when we copied it:
    re-fetching a record that was last edited in 2019 every morning does not
    make it fresh, it just makes the copy recent.
    """
    cutoff = int(time.time()) - STALE_AFTER_DAYS * 86400
    return sql_check(
        conn,
        name="freshness",
        severity=Severity.WARN,
        description=f"products should have been updated upstream within {STALE_AFTER_DAYS // 365} years",
        offenders_sql="""
            SELECT code, name, last_modified_t,
                   CAST((strftime('%s','now') - last_modified_t) / 86400 AS INTEGER) AS days_old
            FROM product
            WHERE last_modified_t IS NOT NULL AND last_modified_t < ?
            ORDER BY last_modified_t ASC
        """,
        examined_sql="SELECT COUNT(*) FROM product WHERE last_modified_t IS NOT NULL",
        params=(cutoff,),
        tolerance=0.40,
    )


def implausible_energy(conn: sqlite3.Connection) -> CheckResult:
    """Energy values outside what food can physically be."""
    return sql_check(
        conn,
        name="implausible_energy",
        severity=Severity.ERROR,
        description=f"energy per 100 g must be between 0 and {MAX_KCAL_100G:.0f} kcal",
        offenders_sql="""
            SELECT code, name, energy_kcal_100g
            FROM product
            WHERE energy_kcal_100g IS NOT NULL
              AND (energy_kcal_100g < 0 OR energy_kcal_100g > ?)
        """,
        examined_sql="SELECT COUNT(*) FROM product WHERE energy_kcal_100g IS NOT NULL",
        params=(MAX_KCAL_100G,),
    )


def impossible_macros(conn: sqlite3.Connection) -> CheckResult:
    """Macros that are negative, or that sum past 100 g in 100 g of food."""
    return sql_check(
        conn,
        name="impossible_macros",
        severity=Severity.ERROR,
        description=f"macros must be non-negative and sum to at most {MACRO_SUM_LIMIT:.0f} g per 100 g",
        offenders_sql="""
            SELECT code, name, proteins_100g, carbohydrates_100g, fat_100g,
                   ROUND(COALESCE(proteins_100g,0) + COALESCE(carbohydrates_100g,0)
                         + COALESCE(fat_100g,0), 2) AS macro_sum
            FROM product
            WHERE proteins_100g < 0 OR carbohydrates_100g < 0 OR fat_100g < 0
               OR (COALESCE(proteins_100g,0) + COALESCE(carbohydrates_100g,0)
                   + COALESCE(fat_100g,0)) > ?
        """,
        examined_sql="""
            SELECT COUNT(*) FROM product
            WHERE proteins_100g IS NOT NULL
               OR carbohydrates_100g IS NOT NULL
               OR fat_100g IS NOT NULL
        """,
        params=(MACRO_SUM_LIMIT,),
    )


def energy_macro_consistency(conn: sqlite3.Connection) -> CheckResult:
    """Declared energy that contradicts the declared macros.

    This is the check worth having. Each field on its own looks perfectly
    reasonable; only the relationship between them is wrong. It catches the
    kJ value pasted into the kcal field, the per-portion figure stored as
    per-100 g, and the decimal point in the wrong place — all of which pass
    every single-column validation and then quietly corrupt everything
    computed downstream.
    """
    return sql_check(
        conn,
        name="energy_macro_consistency",
        severity=Severity.WARN,
        description=(
            "declared energy should be within "
            f"{ENERGY_TOLERANCE:.0%} of 4·protein + 4·carbs + 9·fat"
        ),
        offenders_sql="""
            WITH computed AS (
                SELECT code, name, energy_kcal_100g,
                       (COALESCE(proteins_100g,0) * 4.0
                        + COALESCE(carbohydrates_100g,0) * 4.0
                        + COALESCE(fat_100g,0) * 9.0) AS kcal_from_macros
                FROM product
                WHERE energy_kcal_100g IS NOT NULL
                  AND energy_kcal_100g > 0
                  AND proteins_100g IS NOT NULL
                  AND carbohydrates_100g IS NOT NULL
                  AND fat_100g IS NOT NULL
            )
            SELECT code, name, energy_kcal_100g,
                   ROUND(kcal_from_macros, 1) AS kcal_from_macros,
                   ROUND(ABS(energy_kcal_100g - kcal_from_macros)
                         / energy_kcal_100g, 3) AS relative_error
            FROM computed
            -- Below ~40 kcal the relative error explodes on rounding alone,
            -- so near-zero-energy products are out of scope rather than
            -- permanently flagged.
            WHERE energy_kcal_100g >= 40
              AND ABS(energy_kcal_100g - kcal_from_macros) / energy_kcal_100g > ?
            ORDER BY relative_error DESC
        """,
        examined_sql="""
            SELECT COUNT(*) FROM product
            WHERE energy_kcal_100g IS NOT NULL AND energy_kcal_100g >= 40
              AND proteins_100g IS NOT NULL
              AND carbohydrates_100g IS NOT NULL
              AND fat_100g IS NOT NULL
        """,
        params=(ENERGY_TOLERANCE,),
        tolerance=0.15,
    )


def schema_drift(conn: sqlite3.Connection) -> CheckResult:
    """Fields the pipeline depends on that the API stopped sending.

    Compared against the raw payloads rather than against the parsed columns,
    because a field vanishing upstream and a field the loader never read look
    identical once parsing has happened.

    Measured **per field, not per product**, which is the only unit that makes
    sense here. Open Food Facts omits a field entirely when it is empty for a
    product, so a product with no `additives_tags` key is a product with no
    additives — not a schema change. Drift is a field disappearing from
    essentially every payload at once. Counting products instead would mix the
    two together: a handful of products with empty fields would look like
    partial drift, and real drift would have to be tolerated at some
    indefensible threshold to keep the first case quiet.
    """
    from ..client import PRODUCT_FIELDS

    rows = conn.execute("SELECT code, payload FROM raw_payload").fetchall()
    if not rows:
        return CheckResult(
            name="schema_drift", severity=Severity.ERROR, passed=False,
            offenders=1, examined=0,
            description="no raw payloads stored, cannot compare against the contract",
            samples=[],
        )

    # `code` is always present and `nutriments` is checked separately below.
    required = sorted(f for f in PRODUCT_FIELDS if f not in {"code", "nutriments"})
    payloads = [json.loads(row["payload"]) for row in rows]

    offenders: list[dict[str, Any]] = []
    for name in required:
        absent = sum(1 for payload in payloads if name not in payload)
        if absent / len(payloads) >= FIELD_ABSENT_SHARE:
            offenders.append(
                {
                    "field": name,
                    "absent_from": f"{absent}/{len(payloads)} payloads",
                    "share": f"{absent / len(payloads):.0%}",
                }
            )

    return CheckResult(
        name="schema_drift",
        severity=Severity.ERROR,
        passed=not offenders,
        offenders=len(offenders),
        examined=len(required),
        description=(
            "every requested field should still arrive; a field absent from "
            f"{FIELD_ABSENT_SHARE:.0%}+ of payloads has been dropped upstream"
        ),
        samples=offenders[:MAX_SAMPLES],
    )


def orphan_products(conn: sqlite3.Connection) -> CheckResult:
    """Products with no raw payload: loaded from somewhere untracked."""
    return sql_check(
        conn,
        name="orphan_products",
        severity=Severity.ERROR,
        description="every product must keep the payload it came from",
        offenders_sql="""
            SELECT p.code, p.name
            FROM product p
            LEFT JOIN raw_payload r ON r.code = p.code
            WHERE r.code IS NULL
        """,
        examined_sql="SELECT COUNT(*) FROM product",
    )


CHECKS: tuple[Check, ...] = (
    Check("completeness", Severity.WARN,
          "required fields are present", completeness, ("quality",)),
    Check("volume", Severity.ERROR,
          "the last run loaded what it requested", volume, ("quality",)),
    Check("freshness", Severity.WARN,
          "source records are not stale", freshness, ("quality",)),
    Check("implausible_energy", Severity.ERROR,
          "energy is physically possible", implausible_energy, ("quality",)),
    Check("impossible_macros", Severity.ERROR,
          "macros are physically possible", impossible_macros, ("quality",)),
    Check("energy_macro_consistency", Severity.WARN,
          "energy agrees with macros", energy_macro_consistency, ("quality",)),
    Check("schema_drift", Severity.WARN,
          "the API still sends the agreed fields", schema_drift, ("quality",)),
    Check("orphan_products", Severity.ERROR,
          "products keep their source payload", orphan_products, ("quality",)),
)
