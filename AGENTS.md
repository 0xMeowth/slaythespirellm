# StS2 Text-to-SQL — Project Context

Portfolio project showcasing an agentic engineering workflow: ingest Slay the Spire 2 community run data from the Spire Codex API into a local DB, then build a text-to-SQL pipeline so natural-language questions ("which cards have the highest win rate?") are answered via generated, validated SQL. Also for personal use (the user is an avid StS2 player).

Plan, stage status, and architectural decisions live in ROADMAP.md — read it first. Facts below were verified against the live API on 2026-07-30.

## Data source: Spire Codex

- Site: https://spire-codex.com — community DB + API for StS2 (Steam App ID 2868840)
- API docs (Swagger/OpenAPI): https://spire-codex.com/docs (spec at /openapi.json)
- Source repo (PolyForm Noncommercial 1.0.0): https://github.com/ptrlrd/spire-codex
- Hosted API free within rate limits; portfolio use fine, no commercialization.
- No official API exists from Mega Crit (the game developer); Spire Codex is community-run, data is player-submitted runs (~1.02M as of July 2026).

## Ingestion endpoints (the only two we consume)

1. `GET /api/exports/eng` — ZIP of all reference/game data JSON (cards.json 577, relics.json 296, potions, monsters, encounters, ...). Static game data only, no stats. Seeds dimension tables in one request.
2. `GET /api/exports/runs` — bulk export of runs as a **gzip file** of JSONL (use `gzip.open()`; `curl --compressed` does not decompress it). Params (all optional): `limit`, `start`/`end` (ISO-8601 on server-side submitted_at), `cursor` (opaque keyset token from `X-Next-Cursor` response header, stateless, valid forever). Each line = full raw game JSON as submitted. Lines do NOT contain run_hash/submitted_at/username (server-side metadata) → we compute our own PK.

Other useful endpoints (not ingested): `/api/runs/stats` (ground truth to validate our aggregates), `/api/runs/scores/{entity_type}` (Bayesian tier scores; methodology https://spire-codex.com/leaderboards/scoring), `/api/runs/versions` (game versions), `/api/runs/list` (flattened summaries, total capped at 10k — do not use for ingestion).

## Run JSON shape (schema_version 9, build v0.109.x)

Top level: win, was_abandoned, ascension, game_mode, seed, build_id, run_time, start_time, acts[], killed_by_encounter, killed_by_event, modifiers[], platform_type, schema_version, is_beta.

`players[]` (co-op possible; v1 flattens players[0]): character, deck[] (`{id, current_upgrade_level, floor_added_to_deck}`), relics[] (`{id, floor_added_to_deck, props}`), potions[].

`map_point_history[act][floor]`: map_point_type (monster/elite/boss/shop/rest_site/treasure/unknown/ancient), rooms[] (`{model_id, room_type, turns_taken, monster_ids[]}`), player_stats[] (hp/gold/damage snapshots + choice lists: card_choices `{card, was_picked}`, relic_choices, potion_choices, upgraded_cards, cards_removed, rest_site_choices, event_choices).

Entity ids are prefixed in run data (`CARD.STRIKE_SILENT`, `RELIC.FISHING_ROD`); reference tables use bare ids — strip prefix at insert. Reference descriptions contain `[tag]` markup — strip with `\[/?[a-z]+(?::\d+)?\]`.

## Architecture

1. `ingest/` — fetch + parse + sync into SQLite (`data/spire.db`, gitignored). Tables: runs, run_cards, run_relics, run_card_choices, raw_runs (gzipped raw line), cards, relics, sync_state (key/value, replace), sync_log (append, one row per sync).
2. `nlq/` — text-to-SQL pipeline: schema-prompted generation → validate (parse, single statement, SELECT-only, read-only connection) → execute → self-correct on error (2–3 retries) → NL answer showing the SQL. Router declines strategy questions in v1 (stats only).
3. Web UI (phase 4): thin chat frontend.

## Conventions

- Python ≥3.13, uv-managed. httpx for fetching, sqlite3 stdlib for DB.
- Never write to the remote API (run submission out of scope).
- Be polite to the API: honor X-RateLimit-* headers, back off on 429 Retry-After; 60 req/min anonymous, API key via X-API-Key header gets a dedicated limit.
- Win-rate views use Bayesian shrinkage: shrunk = (wins + W×baseline)/(n + W); raw AVG(win) misleads on rare cards.
