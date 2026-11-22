"""The ontology: which relationships are allowed, and between what.

Keeping this as data rather than scattering it through the loader means the
integrity checks can be derived from it. A new relationship type is declared in
one place and the validator picks it up automatically, instead of the rules
drifting away from the loader one commit at a time.
"""

from __future__ import annotations

from typing import Final

PRODUCT: Final = "product"
BRAND: Final = "brand"
CATEGORY: Final = "category"
INGREDIENT: Final = "ingredient"
ADDITIVE: Final = "additive"

NODE_LABELS: Final[frozenset[str]] = frozenset(
    {PRODUCT, BRAND, CATEGORY, INGREDIENT, ADDITIVE}
)

#: Relationship name -> (allowed source label, allowed destination label).
#: An edge whose endpoints do not match its declared types is a defect even
#: when both nodes exist, which is the "wrong relationship type" case the
#: integrity suite looks for.
RELATIONSHIPS: Final[dict[str, tuple[str, str]]] = {
    "HAS_BRAND": (PRODUCT, BRAND),
    "IN_CATEGORY": (PRODUCT, CATEGORY),
    "CONTAINS_INGREDIENT": (PRODUCT, INGREDIENT),
    "CONTAINS_ADDITIVE": (PRODUCT, ADDITIVE),
}

#: Relationships every product is expected to have. Missing ones are reported
#: as incompleteness rather than as a hard error: Open Food Facts is
#: crowd-sourced and partial records are normal, but a sudden jump in how many
#: products lack a category is exactly the signal worth alerting on.
EXPECTED_PRODUCT_RELATIONSHIPS: Final[tuple[str, ...]] = (
    "IN_CATEGORY",
    "HAS_BRAND",
)

#: Table that stores each node label.
NODE_TABLES: Final[dict[str, str]] = {
    PRODUCT: "product",
    BRAND: "brand",
    CATEGORY: "category",
    INGREDIENT: "ingredient",
    ADDITIVE: "additive",
}

#: Primary-key column for each node label.
NODE_KEYS: Final[dict[str, str]] = {
    PRODUCT: "code",
    BRAND: "id",
    CATEGORY: "id",
    INGREDIENT: "id",
    ADDITIVE: "id",
}
