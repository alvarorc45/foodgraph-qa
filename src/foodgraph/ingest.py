"""Turn API payloads into graph nodes and edges, and load them into SQLite.

The normalising half is kept free of I/O so it can be unit-tested against
recorded payloads without a database or a network, which is what makes the
offline suite fast and deterministic.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from . import ontology as onto

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


@dataclass(slots=True)
class Node:
    label: str
    id: str
    name: str


@dataclass(slots=True)
class Edge:
    src_type: str
    src_id: str
    rel: str
    dst_type: str
    dst_id: str


@dataclass(slots=True)
class ProductRecord:
    """One normalised product, ready to load."""

    code: str
    name: str | None
    quantity: str | None
    energy_kcal_100g: float | None
    proteins_100g: float | None
    carbohydrates_100g: float | None
    fat_100g: float | None
    last_modified_t: int | None
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)


# --------------------------------------------------------------- normalising

def _clean_tag(tag: str) -> tuple[str, str] | None:
    """Split an Open Food Facts tag into (id, display name).

    Tags arrive language-prefixed, like ``en:breakfast-cereals`` or
    ``fr:cereales``. The prefix is dropped for the id so the same concept
    tagged in two languages does not become two nodes, and the slug is turned
    back into something readable for the name.
    """
    tag = tag.strip()
    if not tag:
        return None
    slug = tag.split(":", 1)[1] if ":" in tag else tag
    slug = slug.strip().lower()
    if not slug:
        return None
    name = slug.replace("-", " ").replace("_", " ").strip()
    return slug, name


def _number(value: Any) -> float | None:
    """Coerce an API value to a float, or None.

    Open Food Facts stores numbers as strings often enough that assuming float
    is a reliable way to lose data, and it also uses the empty string for
    "unknown". Booleans are rejected because ``float(True)`` is 1.0, which
    would quietly invent a nutrient value.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", ".")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _energy_kcal(nutriments: dict[str, Any]) -> float | None:
    """Prefer kcal; fall back to converting kJ rather than reporting nothing."""
    kcal = _number(nutriments.get("energy-kcal_100g"))
    if kcal is not None:
        return kcal
    kj = _number(nutriments.get("energy-kj_100g"))
    if kj is None:
        kj = _number(nutriments.get("energy_100g"))
    return round(kj / 4.184, 2) if kj is not None else None


def normalise(payload: dict[str, Any]) -> ProductRecord | None:
    """Build a ProductRecord from one API product payload.

    Returns None when the payload has no barcode: without an identity there is
    nothing to attach edges to, and loading it would create a node that no
    check could ever trace back to a source.
    """
    code = str(payload.get("code") or "").strip()
    if not code:
        return None

    nutriments = payload.get("nutriments")
    if not isinstance(nutriments, dict):
        nutriments = {}

    name = payload.get("product_name")
    record = ProductRecord(
        code=code,
        name=name.strip() if isinstance(name, str) and name.strip() else None,
        quantity=payload.get("quantity") or None,
        energy_kcal_100g=_energy_kcal(nutriments),
        proteins_100g=_number(nutriments.get("proteins_100g")),
        carbohydrates_100g=_number(nutriments.get("carbohydrates_100g")),
        fat_100g=_number(nutriments.get("fat_100g")),
        last_modified_t=int(_number(payload.get("last_modified_t")) or 0) or None,
    )

    tag_sources = (
        ("brands_tags", onto.BRAND, "HAS_BRAND"),
        ("categories_tags", onto.CATEGORY, "IN_CATEGORY"),
        ("ingredients_tags", onto.INGREDIENT, "CONTAINS_INGREDIENT"),
        ("additives_tags", onto.ADDITIVE, "CONTAINS_ADDITIVE"),
    )

    for key, label, rel in tag_sources:
        tags = payload.get(key)
        if not isinstance(tags, list):
            continue
        for tag in tags:
            if not isinstance(tag, str):
                continue
            parsed = _clean_tag(tag)
            if parsed is None:
                continue
            node_id, node_name = parsed
            record.nodes.append(Node(label=label, id=node_id, name=node_name))
            record.edges.append(
                Edge(onto.PRODUCT, code, rel, label, node_id)
            )

    return record


# ------------------------------------------------------------------- loading

def connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn


def load(
    conn: sqlite3.Connection,
    payloads: Iterable[dict[str, Any]],
    *,
    source: str = "api",
) -> dict[str, int]:
    """Load payloads into the graph. Returns counts for the run."""
    started = int(time.time())
    cursor = conn.execute(
        "INSERT INTO ingest_run (started_at, source) VALUES (?, ?)",
        (started, source),
    )
    run_id = cursor.lastrowid

    requested = loaded = failed = 0
    now = int(time.time())

    for payload in payloads:
        requested += 1
        record = normalise(payload)
        if record is None:
            failed += 1
            continue

        conn.execute(
            """
            INSERT INTO product (
                code, name, quantity, energy_kcal_100g, proteins_100g,
                carbohydrates_100g, fat_100g, last_modified_t, ingested_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                name = excluded.name,
                quantity = excluded.quantity,
                energy_kcal_100g = excluded.energy_kcal_100g,
                proteins_100g = excluded.proteins_100g,
                carbohydrates_100g = excluded.carbohydrates_100g,
                fat_100g = excluded.fat_100g,
                last_modified_t = excluded.last_modified_t,
                ingested_at = excluded.ingested_at
            """,
            (
                record.code, record.name, record.quantity,
                record.energy_kcal_100g, record.proteins_100g,
                record.carbohydrates_100g, record.fat_100g,
                record.last_modified_t, now,
            ),
        )
        conn.execute(
            """
            INSERT INTO raw_payload (code, payload, fetched_at) VALUES (?, ?, ?)
            ON CONFLICT(code) DO UPDATE SET
                payload = excluded.payload, fetched_at = excluded.fetched_at
            """,
            (record.code, json.dumps(payload, sort_keys=True), now),
        )

        for node in record.nodes:
            table = onto.NODE_TABLES[node.label]
            conn.execute(
                f"INSERT INTO {table} (id, name) VALUES (?, ?) "
                f"ON CONFLICT(id) DO NOTHING",
                (node.id, node.name),
            )
        for edge in record.edges:
            conn.execute(
                "INSERT INTO edge (src_type, src_id, rel, dst_type, dst_id) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (edge.src_type, edge.src_id, edge.rel, edge.dst_type, edge.dst_id),
            )
        loaded += 1

    conn.execute(
        "UPDATE ingest_run SET finished_at = ?, requested = ?, loaded = ?, failed = ? "
        "WHERE id = ?",
        (int(time.time()), requested, loaded, failed, run_id),
    )
    conn.commit()
    return {"requested": requested, "loaded": loaded, "failed": failed}
