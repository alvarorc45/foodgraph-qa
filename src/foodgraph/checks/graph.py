"""Structural integrity of the graph.

Nothing here cares whether a calorie figure is plausible, only whether the
graph is well formed. Each rule is a query returning the offending rows -
the graph lives in a relational store, so "every endpoint resolves to a node"
is a LEFT JOIN rather than a walk in Python.
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
    """Edges pointing at a node that doesn't exist, on either end.

    The classic loading defect - edge written before the node, or the node
    load failed and nobody noticed because every query happened to use an
    inner join.
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
    """Unknown relationship, or one used between the wrong labels.

    Both nodes can exist and the edge still be nonsense. CONTAINS_INGREDIENT
    pointing at a brand is a perfectly well-formed row that means nothing,
    and only checking the triple against the ontology finds it.
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
    """Two ids denoting one thing.

    The primary key stops an id repeating, so duplicates turn up as two ids
    sharing a normalised name - `coca-cola` against `coca_cola`, or a
    trailing space. The entity's edges end up split between the two nodes and
    every aggregate over it is quietly halved.
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
    """A node related to itself. Always a loader bug in this model."""
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
    """Non-product nodes nothing points at.

    Harmless in itself, but it's the fingerprint of a half-finished load or a
    deleted product that left its ingredients behind, and it inflates every
    count over those tables.
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
    """Products missing a relationship the ontology expects.

    Warning rather than error. Plenty of crowd-sourced products genuinely
    have no category yet; what matters is the proportion moving. The
    judgement is in the tolerance, not in the rule.
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


# How much of the tag vocabulary can stay unresolved before the graph stops
# being safe to aggregate over. A judgement call rather than a measurement -
# there's no published figure, and this wants re-tuning once we have a
# baseline. Ten percent is roughly where "a few odd entries" turns into "a
# tenth of your entities are strings nobody can join on".
UNRESOLVED_SHARE = 0.10

# In every payload we've sampled, tags that matched the taxonomy came back as
# `en:` and raw contributor text kept its original prefix. Inferred from the
# data rather than from documented behaviour, hence WARN and not ERROR.
RESOLVED_LANG = "en"


def unresolved_entities(conn: sqlite3.Connection) -> CheckResult:
    """Nodes the source itself couldn't resolve to its taxonomy.

    OFF rewrites a tag to `en:<slug>` when it matches the taxonomy and passes
    the contributor's raw text through otherwise, prefix and all. The prefix
    that survived is therefore a verdict from upstream.

    This is where the junk lives. On live data the unresolved nodes aren't
    ingredients at all - storage and heating instructions, marketing copy,
    nutrition-table headings, a manufacturer's postal address, health
    warnings, and strings that read like two ingredients run together where
    the comma was lost. Plenty carry character substitutions
    (`casejnato-de-sqdio`, `flsfato-de-potasio`) of the kind text recognition
    produces, though nothing in the data says how they were entered.

    Ordered by `referenced_by` ascending, because a real ingredient is shared
    and a one-off string is not. That puts the likely garbage first.
    """
    tables = [(label, table) for label, table in sorted(onto.NODE_TABLES.items())
              if label != onto.PRODUCT]
    unresolved = " UNION ALL ".join(
        f"SELECT '{label}' AS label, id, name, lang FROM {table} "
        f"WHERE lang IS NOT NULL AND lang <> '{RESOLVED_LANG}'"
        for label, table in tables
    )
    total = " UNION ALL ".join(f"SELECT id FROM {table}" for _, table in tables)

    return sql_check(
        conn,
        name="unresolved_entities",
        severity=Severity.WARN,
        description=(
            "tags should resolve to the upstream taxonomy; one that kept a "
            "non-English prefix is raw contributor text"
        ),
        offenders_sql=f"""
            WITH unresolved AS ({unresolved})
            SELECT u.label, u.lang, u.id, u.name,
                   COUNT(DISTINCT e.src_id) AS referenced_by
            FROM unresolved u
            LEFT JOIN edge e ON e.dst_type = u.label AND e.dst_id = u.id
            GROUP BY u.label, u.id
            ORDER BY referenced_by ASC, LENGTH(u.name) DESC
        """,
        examined_sql=f"SELECT COUNT(*) FROM ({total})",
        tolerance=UNRESOLVED_SHARE,
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
    Check("unresolved_entities", Severity.WARN,
          "tags resolve to the upstream taxonomy",
          unresolved_entities, ("graph",)),
)
