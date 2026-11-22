-- Property graph over Open Food Facts, in SQLite.
--
-- Modelled as a graph rather than one wide table because the entities really
-- are one. Products share ingredients, categories and brands, and flattening
-- that into columns hides the defects we're looking for.
--
-- One table per node label, all relationships in a single typed `edge` table.
-- Keeping edges in one table is what lets integrity rules stay plain SQL --
-- "every endpoint resolves to a node" is one LEFT JOIN rather than one query
-- per relationship type.

PRAGMA foreign_keys = ON;

-- nodes ---------------------------------------------------------------

CREATE TABLE IF NOT EXISTS product (
    code TEXT PRIMARY KEY, -- barcode (EAN/UPC)
    name TEXT,
    quantity TEXT,
    -- Per 100 g as published. Nullable on purpose; absence is itself a
    -- completeness signal and coercing it to 0 would invent facts.
    energy_kcal_100g REAL,
    proteins_100g REAL,
    carbohydrates_100g REAL,
    fat_100g REAL,
    last_modified_t INTEGER, -- epoch secs, last contributor edit
    ingested_at INTEGER NOT NULL -- epoch secs, when we pulled it
);

-- `lang` on the tag-derived nodes is the language prefix the API sent
-- (`en:sugar` -> 'en'), kept because it carries an entity-resolution signal.
-- In every payload we've sampled, tags that matched the English taxonomy came
-- back as `en:` and raw contributor text kept its original prefix. An earlier
-- version of the loader dropped the prefix as noise, which left free text
-- indistinguishable from a real entity.

CREATE TABLE IF NOT EXISTS brand (
    id TEXT PRIMARY KEY, -- normalised slug
    name TEXT NOT NULL,
    lang TEXT
);

CREATE TABLE IF NOT EXISTS category (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lang TEXT
);

CREATE TABLE IF NOT EXISTS ingredient (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lang TEXT
);

CREATE TABLE IF NOT EXISTS additive (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lang TEXT
);

-- edges ---------------------------------------------------------------

-- No foreign keys here, and that's deliberate. If the database refused to
-- store a broken edge, orphan_edges could never find one and would be
-- untestable. Upstream gives no such guarantee anyway. Permissive loader,
-- strict checks.
CREATE TABLE IF NOT EXISTS edge (
    src_type TEXT NOT NULL,
    src_id TEXT NOT NULL,
    rel TEXT NOT NULL,
    dst_type TEXT NOT NULL,
    dst_id TEXT NOT NULL,
    PRIMARY KEY (src_type, src_id, rel, dst_type, dst_id)
);

CREATE INDEX IF NOT EXISTS idx_edge_dst ON edge (dst_type, dst_id);
CREATE INDEX IF NOT EXISTS idx_edge_rel ON edge (rel);

-- raw payloads + run history ------------------------------------------

-- The untouched response for every product. Schema drift is detected against
-- what the API actually sent, not against the parsed columns -- once parsing
-- has happened, a field that vanished upstream and a field the loader never
-- read look exactly the same.
CREATE TABLE IF NOT EXISTS raw_payload (
    code TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    fetched_at INTEGER NOT NULL
);

-- One row per ingestion, so volume and freshness have a baseline to compare
-- against instead of a hardcoded guess.
CREATE TABLE IF NOT EXISTS ingest_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    finished_at INTEGER,
    requested INTEGER NOT NULL DEFAULT 0,
    loaded INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL
);
