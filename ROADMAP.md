# ROADMAP — StS2 Text-to-SQL

Talk-to-the-database agent over Slay the Spire 2 community run data
(Spire Codex API → SQLite → text-to-SQL pipeline → web UI).

## Progress Tracker

| Stage | Status | Placeholder | Notes |
|-------|--------|-------------|-------|
| 1a Project scaffold | done | none | git init, uv (py3.14), ingest/ + nlq/, CLAUDE.md written to disk |
| 1b SQLite schema | done | none | schema.sql + ingest/db.py; 9 tables verified |
| 2a Reference ingest | pending | none | /api/exports/eng → cards, relics |
| 2b Runs fetcher | pending | none | cursor loop, gzip JSONL, rate-limit aware |
| 2c Run parser | pending | none | JSON → runs, run_cards, run_relics, run_card_choices |
| 2d Test pull (1k runs) | pending | none | validate vs /runs/stats |
| 2e Full pull (100k recent) | pending | none | start=<date>, ~250MB download |
| 2f Incremental sync + cron | pending | none | sync_state cursor, sync_log |
| 3a Schema prompt builder | pending | none | DDL + sample rows for LLM; grill-me phase 3 first |
| 3b SQL validator | pending | none | single stmt, SELECT-only, read-only conn |
| 3c Generate + retry loop | pending | none | error feedback, 2–3 retries |
| 3d Answer synthesis | pending | none | NL answer + show SQL |
| 3e Router | pending | none | stats (SQL) vs strategy (declined in v1) |
| 3f Eval set | pending | none | 10 questions with expected answers |
| 4a Chat web UI | pending | none | thin frontend over nlq |
| 4b Deploy | pending | none | portfolio hosting TBD |

## Phase Order

| Phase | Items | Key Dependency |
|-------|-------|----------------|
| 1 Foundation | 1a–1b | — |
| 2 Ingestion | 2a–2f | 1b schema |
| 3 Text-to-SQL | 3a–3f | 2d data present |
| 4 Web UI | 4a–4b | 3 pipeline works |

## Phase 1 — Foundation

- **1a Project scaffold**: git init, `ingest/` + `nlq/` packages, uv project (Python 3.13), .gitignore.
  **Verification:** `uv run python -c "import ingest, nlq"`
- **1b SQLite schema**: `schema.sql` with runs, run_cards, run_relics, run_card_choices, raw_runs, cards, relics, sync_state, sync_log + indexes; `ingest/db.py` applies it.
  **Verification:** `uv run python -m ingest.db && sqlite3 data/spire.db .tables`

## Phase 2 — Ingestion

- **2a Reference ingest**: fetch `/api/exports/eng` ZIP → cards, relics tables; strip `[tag]` markup.
  **Verification:** `sqlite3 data/spire.db "SELECT COUNT(*) FROM cards"` ≈ 577
- **2b Runs fetcher**: stream `/api/exports/runs` (gzip JSONL), cursor pagination, honor rate-limit headers, backoff on 429.
  **Verification:** fetch 100 runs to stdout count
- **2c Run parser**: export line → rows in runs, run_cards, run_relics, run_card_choices, raw_runs.
  **Verification:** parse sample line, spot-check counts vs raw JSON
- **2d Test pull (1k runs)**: end-to-end sync of ~1000 runs; compare win rate vs `/api/runs/stats`.
  **Verification:** `sqlite3 data/spire.db "SELECT COUNT(*), AVG(win) FROM runs"`
- **2e Full pull (100k recent)**: `start=` cutoff date, ~250MB download.
  **Verification:** row count ≥ 100k; sync_log row status=ok
- **2f Incremental sync + cron**: resume from sync_state cursor; sync_log start/finish rows; crontab entry.
  **Verification:** run sync twice, second run fetches only new runs, no duplicate PKs

## Phase 3 — Text-to-SQL (run grill-me before starting)

- **3a Schema prompt builder**: DDL + N sample rows per table → system prompt.
- **3b SQL validator**: sqlglot parse, single statement, SELECT-only; read-only connection.
- **3c Generate + retry loop**: on SQL error feed error back, max 2–3 retries.
- **3d Answer synthesis**: result rows → NL answer, show SQL alongside.
- **3e Router**: classify stats vs strategy; decline strategy in v1.
- **3f Eval set**: 10 hand-written questions with expected answers.
  **Verification (phase):** eval script passes ≥ 8/10

## Phase 4 — Web UI

- **4a Chat web UI**: thin frontend over nlq pipeline.
- **4b Deploy**: hosting TBD.

## Architectural Notes

- Ingest from `/api/exports/runs` (gzipped JSONL, full run detail, cursor pagination) —
  NOT `/runs/list` (summaries only, total capped at 10k).
- Export lines lack run_hash/submitted_at → PK = SHA-256 of raw line;
  timestamp tracked via `X-Next-Cursor` (decodes to `submitted_at|run_hash` of last run in page).
- Export body is a gzip file (not transport compression) — `gzip.open()`, not `--compressed`.
- Incremental sync: save last cursor in sync_state (replace); INSERT OR IGNORE for idempotency;
  sync_log appends one row per sync run (start/finish/count/status).
- Store raw gzipped JSON in raw_runs for future re-parsing; schema_version varies across patches.
- Rate limits: 60 req/min anonymous; API key via `X-API-Key` (profile page) gets dedicated limit;
  honor `X-RateLimit-*` headers, back off on 429 `Retry-After`.
- Win-rate views use Bayesian shrinkage (mirror Codex Score): shrunk = (wins + W×baseline)/(n + W).
- Strip `\[/?[a-z]+(?::\d+)?\]` markup from descriptions at insert.
- Run ids in run data are prefixed (`CARD.X`, `RELIC.Y`); reference tables use bare ids — strip prefix at insert.
- ~28KB/run raw, 2.5KB gzipped; 100k runs ≈ 250MB download. ~1.02M total runs, 26.5% win rate (2026-07-30).
