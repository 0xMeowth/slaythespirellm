-- Reference tables (from /api/exports/eng)

CREATE TABLE IF NOT EXISTS cards (
    card_id     TEXT PRIMARY KEY,          -- bare id, e.g. STRIKE_SILENT
    name        TEXT NOT NULL,
    description TEXT,                      -- markup stripped
    cost        INTEGER,                   -- NULL for X-cost/statuses
    is_x_cost   INTEGER,
    type        TEXT,                      -- Attack / Skill / Power / ...
    rarity      TEXT,
    color       TEXT,                      -- character pool
    target      TEXT,
    keywords    TEXT                       -- comma-joined, nullable
);

CREATE TABLE IF NOT EXISTS relics (
    relic_id    TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    rarity      TEXT,
    pool        TEXT
);

-- Fact tables (from /api/exports/runs)

CREATE TABLE IF NOT EXISTS runs (
    run_id            TEXT PRIMARY KEY,    -- sha256 of raw export line
    character         TEXT NOT NULL,       -- bare id from players[0]
    win               INTEGER NOT NULL,    -- 0/1
    was_abandoned     INTEGER NOT NULL,    -- 0/1; deaths are win=0 AND was_abandoned=0
    ascension         INTEGER NOT NULL,
    game_mode         TEXT,
    seed              TEXT,
    build_id          TEXT,                -- game version, e.g. v0.109.1
    run_time          INTEGER,             -- seconds
    start_time        INTEGER,             -- unix epoch, player-local
    submitted_at      TEXT,                -- ISO-8601, approx (from page cursor)
    floors_reached    INTEGER,             -- count of map points visited
    acts              TEXT,                -- comma-joined act ids
    killed_by_encounter TEXT,              -- bare id, NULL if not killed by encounter
    killed_by_event   TEXT,
    platform_type     TEXT,
    schema_version    INTEGER,
    is_beta           INTEGER
);
CREATE INDEX IF NOT EXISTS idx_runs_character ON runs(character);
CREATE INDEX IF NOT EXISTS idx_runs_win ON runs(win);
CREATE INDEX IF NOT EXISTS idx_runs_ascension ON runs(ascension);
CREATE INDEX IF NOT EXISTS idx_runs_build ON runs(build_id);

-- Final deck: one row per physical card copy
CREATE TABLE IF NOT EXISTS run_cards (
    run_id        TEXT NOT NULL REFERENCES runs(run_id),
    card_id       TEXT NOT NULL,
    upgrade_level INTEGER NOT NULL DEFAULT 0,
    floor_added   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_run_cards_run ON run_cards(run_id);
CREATE INDEX IF NOT EXISTS idx_run_cards_card ON run_cards(card_id);

CREATE TABLE IF NOT EXISTS run_relics (
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    relic_id    TEXT NOT NULL,
    floor_added INTEGER
);
CREATE INDEX IF NOT EXISTS idx_run_relics_run ON run_relics(run_id);
CREATE INDEX IF NOT EXISTS idx_run_relics_relic ON run_relics(relic_id);

-- Card offers: what was shown vs taken (from map_point_history)
CREATE TABLE IF NOT EXISTS run_card_choices (
    run_id      TEXT NOT NULL REFERENCES runs(run_id),
    act         INTEGER NOT NULL,          -- 1-based
    floor_index INTEGER NOT NULL,          -- 1-based within act
    card_id     TEXT NOT NULL,
    was_picked  INTEGER NOT NULL           -- 0/1
);
CREATE INDEX IF NOT EXISTS idx_choices_run ON run_card_choices(run_id);
CREATE INDEX IF NOT EXISTS idx_choices_card ON run_card_choices(card_id);

-- Raw export lines, gzipped, for future re-parsing
CREATE TABLE IF NOT EXISTS raw_runs (
    run_id  TEXT PRIMARY KEY,
    line_gz BLOB NOT NULL
);

-- Sync bookkeeping

CREATE TABLE IF NOT EXISTS sync_state (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS sync_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    runs_fetched INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL,             -- running / ok / failed
    error        TEXT
);
