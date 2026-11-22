-- Knowledge graph for Open Food Facts, stored in SQLite.
--
-- The data is modelled as a property graph rather than one wide table, because
-- the entities are genuinely a graph: a product is made of ingredients, carries
-- additives, belongs to categories and is sold by brands, and those entities are
-- shared across products. Flattening that into columns would hide exactly the
-- defects this harness looks for (an ingredient that no product references, a
-- category edge pointing at a brand, the same entity under two ids).
--
-- Nodes live in one table per label; every relationship lives in a single typed
-- `edge` table. One edge table keeps integrity checks expressible as plain SQL:
-- "every edge endpoint must resolve to a node" becomes one LEFT JOIN instead of
-- one query per relationship type.

PRAGMA foreign_keys = ON;

-- --------------------------------------------------------------------- nodes

CREATE TABLE IF NOT EXISTS product (
    code              TEXT PRIMARY KEY,         -- barcode (EAN/UPC)
    name              TEXT,
    quantity          TEXT,
    -- Per 100 g, as published. Nullable on purpose: absence is itself a
    -- completeness signal, and coercing it to 0 would invent facts.
    energy_kcal_100g  REAL,
    proteins_100g     REAL,
    carbohydrates_100g REAL,
    fat_100g          REAL,
    -- Epoch seconds from the API: when the contributor last edited it.
    last_modified_t   INTEGER,
    -- Epoch seconds: when this harness pulled it. Freshness compares the two.
    ingested_at       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS brand (
    id   TEXT PRIMARY KEY,                      -- normalised slug
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS category (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ingredient (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS additive (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL
);

-- --------------------------------------------------------------------- edges

-- Deliberately WITHOUT foreign keys.
--
-- That looks wrong until you remember what this table is for. If the database
-- enforced referential integrity, a broken edge could never be stored, and the
-- orphan-edge check would be untestable and pointless. Real pipelines load
-- facts from upstream systems that have no such guarantee, and the job of a
-- quality harness is to FIND the breakage, not to make it unrepresentable.
-- So the loader is permissive and the checks are strict.
CREATE TABLE IF NOT EXISTS edge (
    src_type TEXT NOT NULL,
    src_id   TEXT NOT NULL,
    rel      TEXT NOT NULL,
    dst_type TEXT NOT NULL,
    dst_id   TEXT NOT NULL,
    PRIMARY KEY (src_type, src_id, rel, dst_type, dst_id)
);

CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge (dst_type, dst_id);
CREATE INDEX IF NOT EXISTS idx_edge_rel ON edge (rel);

-- ------------------------------------------------------------ raw + metadata

-- The untouched API payload for every product, kept so schema drift can be
-- detected against what the API actually sent rather than against what the
-- loader managed to parse. Without this, a field silently disappearing
-- upstream would look identical to a field the loader never read.
CREATE TABLE IF NOT EXISTS raw_payload (
    code        TEXT PRIMARY KEY,
    payload     TEXT NOT NULL,
    fetched_at  INTEGER NOT NULL
);

-- One row per ingestion run, so volume and freshness have a baseline to
-- compare against instead of a hardcoded guess.
CREATE TABLE IF NOT EXISTS ingest_run (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at  INTEGER NOT NULL,
    finished_at INTEGER,
    requested   INTEGER NOT NULL DEFAULT 0,
    loaded      INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL
);
