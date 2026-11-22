"""What relationships are allowed, and between what.

Kept as data so the integrity checks can be derived from it instead of each
rule hardcoding its own idea of the model.
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

# rel -> (source label, destination label). An edge whose endpoints don't
# match its declared types is a defect even when both nodes exist.
RELATIONSHIPS: Final[dict[str, tuple[str, str]]] = {
    "HAS_BRAND": (PRODUCT, BRAND),
    "IN_CATEGORY": (PRODUCT, CATEGORY),
    "CONTAINS_INGREDIENT": (PRODUCT, INGREDIENT),
    "CONTAINS_ADDITIVE": (PRODUCT, ADDITIVE),
}

# Relationships every product ought to have. Missing ones are incompleteness,
# not a hard error - partial records are normal in crowd-sourced data. A jump
# in how many products lack a category is the thing worth alerting on.
EXPECTED_PRODUCT_RELATIONSHIPS: Final[tuple[str, ...]] = (
    "IN_CATEGORY",
    "HAS_BRAND",
)

NODE_TABLES: Final[dict[str, str]] = {
    PRODUCT: "product",
    BRAND: "brand",
    CATEGORY: "category",
    INGREDIENT: "ingredient",
    ADDITIVE: "additive",
}

NODE_KEYS: Final[dict[str, str]] = {
    PRODUCT: "code",
    BRAND: "id",
    CATEGORY: "id",
    INGREDIENT: "id",
    ADDITIVE: "id",
}
