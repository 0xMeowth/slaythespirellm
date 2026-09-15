CREATE TABLE cards (
    card_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description VARCHAR,
    cost BIGINT,
    is_x_cost BIGINT,
    type VARCHAR,
    rarity VARCHAR,
    color VARCHAR,
    target VARCHAR,
    keywords VARCHAR
);

CREATE TABLE relics (
    relic_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description VARCHAR,
    rarity VARCHAR,
    pool VARCHAR
);

CREATE TABLE runs (
    run_id VARCHAR PRIMARY KEY,
    character VARCHAR NOT NULL,
    win BIGINT NOT NULL,
    was_abandoned BIGINT NOT NULL,
    ascension BIGINT NOT NULL,
    game_mode VARCHAR,
    seed VARCHAR,
    build_id VARCHAR,
    run_time BIGINT,
    start_time BIGINT,
    submitted_at VARCHAR,
    floors_reached BIGINT,
    acts VARCHAR,
    killed_by_encounter VARCHAR,
    killed_by_event VARCHAR,
    platform_type VARCHAR,
    schema_version BIGINT,
    is_beta BIGINT
);

CREATE TABLE run_cards (
    run_id VARCHAR NOT NULL REFERENCES runs(run_id),
    card_id VARCHAR NOT NULL,
    upgrade_level BIGINT NOT NULL DEFAULT 0,
    floor_added BIGINT
);

CREATE TABLE run_relics (
    run_id VARCHAR NOT NULL REFERENCES runs(run_id),
    relic_id VARCHAR NOT NULL,
    floor_added BIGINT
);

CREATE TABLE run_card_choices (
    run_id VARCHAR NOT NULL REFERENCES runs(run_id),
    act BIGINT NOT NULL,
    floor_index BIGINT NOT NULL,
    card_id VARCHAR NOT NULL,
    was_picked BIGINT NOT NULL
);
