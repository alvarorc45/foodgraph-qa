"""Integrity of the knowledge graph itself.

These are structural rules: they do not care whether a calorie figure is
plausible, only whether the graph is well formed. Each one is a SQL query that
returns the offending rows, because the graph lives in a relational store and
expressing "every edge endpoint must resolve to a node" as a LEFT JOIN is both
shorter and faster than walking it in Python.
"""

from __future__ import annotations

import sqlite3

from .. import ontology as onto
from .base import Check, CheckResult, Severity, sql_check

# Union of every node table, so endpoint resolution is one query rather than
# one per label. Built from the ontology so a new node type cannot be added
# without these checks noticing it.
_ALL_NODES = " UNION ALL ".join(
    f"SELECT '{label}' AS label, {onto.NODE_KEYS[label]} AS id FROM {table}"
    for label, table in sorted(onto.NODE_TABLES.items())
)

_VALID_RELS = ", ".join(f"'{rel}'" for rel in sorted(onto.RELATIONSHIPS))

# (rel, src_type, dst_type) triples the ontology permits.
_VALID_TRIPLES = " UNION ALL ".join(
    f"SELECT '{rel}' AS rel, '{src}' AS src_type, '{dst}' AS dst_type"
    for rel, (src, dst) in sorted(onto.RELATIONSHIPS.items())
)


def orphan_edges(conn: sqlite3.Connection) -> CheckResult:
    """Edges pointing at a node that does not exist, on either end.

    The classic graph-loading defect: the edge is written before the node, or
    the node load fails and nobody notices because the edge still resolves in
    a join that happens to be inner.
    """
    return sql_check(
        conn,
        name="orphan_edges",
        severity=Severity.ERROR,
        description="every edge endpoint must resolve to an existing node",
        offenders_sql=f"""
            WITH nodes AS ({_ALL_NODES})
            SELECT e.src_type, e.src_id, e.rel, e.dst_type, e.dst_id,
                   CASE WHEN s.id IS NULL THEN 'source' ELSE 'destination' END AS missing_end
            FROM edge e
            LEFT JOIN nodes s ON s.label = e.src_type AND s.id = e.src_id
            LEFT JOIN nodes d ON d.label = e.dst_type AND d.id = e.dst_id
            WHERE s.id IS NULL OR d.id IS NULL
        """,
        examined_sql="SELECT COUNT(*) FROM edge",
    )


def invalid_relationship_type(conn: sqlite3.Connection) -> CheckResult:
    """Edges whose relationship is unknown, or used between the wrong labels.

    Both nodes can exist and the edge still be wrong: CONTAINS_INGREDIENT
    pointing at a brand is a well-formed row and a meaningless fact. Checking
    the triple against the ontology is what catches it.
    """
    return sql_check(
        conn,
        name="invalid_relationship_type",
        severity=Severity.ERROR,
        description="relationships must exist in the ontology and connect the declared labels",
        offenders_sql=f"""
            WITH allowed AS ({_VALID_TRIPLES})
            SELECT e.src_type, e.src_id, e.rel, e.dst_type, e.dst_id
            FROM edge e
            LEFT JOIN allowed a
                   ON a.rel = e.rel
                  AND a.src_type = e.src_type
                  AND a.dst_type = e.dst_type
            WHERE a.rel IS NULL
        """,
        examined_sql="SELECT COUNT(*) FROM edge",
    )


def unknown_node_label(conn: sqlite3.Connection) -> CheckResult:
    """Edges referring to a label the ontology does not define at all."""
    labels = ", ".join(f"'{label}'" for label in sorted(onto.NODE_LABELS))
    return sql_check(
        conn,
        name="unknown_node_label",
        severity=Severity.ERROR,
        description="edge endpoints must use a declared node label",
        offenders_sql=f"""
            SELECT src_type, src_id, rel, dst_type, dst_id
            FROM edge
            WHERE src_type NOT IN ({labels}) OR dst_type NOT IN ({labels})
        """,
        examined_sql="SELECT COUNT(*) FROM edge",
    )


def duplicate_nodes(conn: sqlite3.Connection) -> CheckResult:
    """Distinct ids that denote the same thing.

    The primary key stops the same id appearing twice, so duplicates show up
    as two ids with one normalised name: ``coca-cola`` and ``coca_cola``, or a
    trailing space. In a knowledge graph this silently splits a product's
    relationships across two nodes, so every aggregate over that entity is
    wrong and nothing ever errors.
    """
    unions = " UNION ALL ".join(
        f"SELECT '{label}' AS label, id, "
        f"TRIM(LOWER(REPLACE(REPLACE(name, '-', ' '), '_', ' '))) AS norm "
        f"FROM {table}"
        for label, table in sorted(onto.NODE_TABLES.items())
        if label != onto.PRODUCT  # products are keyed by barcode, not by name
    )
    return sql_check(
        conn,
        name="duplicate_nodes",
        severity=Severity.WARN,
        description="one real-world entity should not exist under two node ids",
        offenders_sql=f"""
            WITH named AS ({unions})
            SELECT label, norm AS normalised_name,
                   COUNT(*) AS copies,
                   GROUP_CONCAT(id, ' | ') AS ids
            FROM named
            WHERE norm <> ''
            GROUP BY label, norm
            HAVING COUNT(*) > 1
        """,
        examined_sql=f"SELECT COUNT(*) FROM ({unions})",
    )


def self_referencing_edges(conn: sqlite3.Connection) -> CheckResult:
    """A node related to itself: always a loader bug, never a real fact here."""
    return sql_check(
        conn,
        name="self_referencing_edges",
        severity=Severity.ERROR,
        description="no node may be related to itself",
        offenders_sql="""
            SELECT src_type, src_id, rel, dst_type, dst_id
            FROM edge
            WHERE src_type = dst_type AND src_id = dst_id
        """,
        examined_sql="SELECT COUNT(*) FROM edge",
    )


def unreachable_nodes(conn: sqlite3.Connection) -> CheckResult:
    """Non-product nodes that no edge points to.

    Harmless on its own, but it is the fingerprint of a half-finished load or
    of a deleted product leaving its ingredients behind, and it inflates every
    count taken over those tables.
    """
    unions = " UNION ALL ".join(
        f"SELECT '{label}' AS label, id FROM {table}"
        for label, table in sorted(onto.NODE_TABLES.items())
        if label != onto.PRODUCT
    )
    return sql_check(
        conn,
        name="unreachable_nodes",
        severity=Severity.WARN,
        description="every non-product node should be referenced by at least one edge",
        offenders_sql=f"""
            WITH nodes AS ({unions})
            SELECT n.label, n.id
            FROM nodes n
            LEFT JOIN edge e ON e.dst_type = n.label AND e.dst_id = n.id
            WHERE e.dst_id IS NULL
        """,
        examined_sql=f"SELECT COUNT(*) FROM ({unions})",
    )


def missing_expected_relationship(conn: sqlite3.Connection) -> CheckResult:
    """Products lacking a relationship the ontology expects them to have.

    A warning, not an error: on a crowd-sourced dataset some products
    genuinely have no category yet. What matters is the proportion moving, so
    the tolerance is where the judgement lives, not the rule.
    """
    rels = ", ".join(f"'{rel}'" for rel in onto.EXPECTED_PRODUCT_RELATIONSHIPS)
    return sql_check(
        conn,
        name="missing_expected_relationship",
        severity=Severity.WARN,
        description=f"products should have each of: {', '.join(onto.EXPECTED_PRODUCT_RELATIONSHIPS)}",
        offenders_sql=f"""
            WITH expected(rel) AS (VALUES {', '.join(f"('{r}')" for r in onto.EXPECTED_PRODUCT_RELATIONSHIPS)})
            SELECT p.code, p.name, x.rel AS missing_relationship
            FROM product p
            CROSS JOIN expected x
            LEFT JOIN edge e
                   ON e.src_type = 'product' AND e.src_id = p.code AND e.rel = x.rel
            WHERE e.src_id IS NULL
        """,
        examined_sql=(
            f"SELECT COUNT(*) * {len(onto.EXPECTED_PRODUCT_RELATIONSHIPS)} FROM product"
        ),
        tolerance=0.30,
    )


CHECKS: tuple[Check, ...] = (
    Check("orphan_edges", Severity.ERROR,
          "edge endpoints resolve to real nodes", orphan_edges, ("graph",)),
    Check("invalid_relationship_type", Severity.ERROR,
          "relationships match the ontology", invalid_relationship_type, ("graph",)),
    Check("unknown_node_label", Severity.ERROR,
          "edge labels are declared", unknown_node_label, ("graph",)),
    Check("self_referencing_edges", Severity.ERROR,
          "no self loops", self_referencing_edges, ("graph",)),
    Check("duplicate_nodes", Severity.WARN,
          "no duplicated entities", duplicate_nodes, ("graph",)),
    Check("unreachable_nodes", Severity.WARN,
          "no dangling nodes", unreachable_nodes, ("graph",)),
    Check("missing_expected_relationship", Severity.WARN,
          "products carry their expected relationships",
          missing_expected_relationship, ("graph",)),
)
