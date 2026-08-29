# ROADMAP — StS2 Text-to-SQL

Talk-to-the-database agent over Slay the Spire 2 community run data
(Spire Codex API → SQLite → text-to-SQL pipeline → web UI).

## Progress Tracker

| Stage | Status | Placeholder | Notes |
|-------|--------|-------------|-------|
| 1a Project scaffold | done | none | git init, uv (py3.14), ingest/ + nlq/, CLAUDE.md written to disk |
| 1b SQLite schema | done | none | schema.sql + ingest/db.py; 9 tables verified |
| 2a Reference ingest | done | none | sync_card_relic_data.py; 577 cards, 296 relics |
| 2b Runs fetcher | done | none | fetch_runs_data.py; cursor loop, 429 backoff |
| 2c Run parser | done | none | parse_runs_data.py; sha256 PK dedups export's own duplicate lines (~30%!) |
| 2d Test pull (1k runs) | done | none | 1374 runs; winrate 26.5% matches /runs/stats; cursor resume verified |
| 2e Full pull | done | none | 659,515 runs (Jun 1–Jul 29); stopped early by choice, cron closes the gap |
| 2f Incremental sync + cron | in-progress | none | cursor resume + sync_log implemented and tested; cron entry pending |
| 3a Offline evaluation | done | none | compact frozen snapshot; 10 development cases; deterministic scoring/reports; 65 tests; held-out split awaits expansion to 40 cases |
| 3b Model + schema context | done | none | 204 automated tests pass; schema context SHA-256 `3cdf1bb425f7cdad4a7d19611ac754a1b0196066acf559d84ce4abf622f95d45`; live check passed with `sea_lion` model `aisingapore/Qwen-SEA-LION-v4.5-27B-IT` |
| 3c SQL guardrails | done | none | SQLGlot policy + SQLite authorizer; read-only execution, timeout, row/byte limits, adversarial matrix; 310 tests |
| 3d LangGraph pipeline | pending | none | explicit StateGraph; router → generate → validate → execute → retry; required Studio walkthrough with the configured real model |
| 3e End-to-end baseline eval | pending | none | connect the real-model LangGraph pipeline to the offline evaluator; 3 independent trials per case |
| 3f Answer synthesis | pending | none | result rows → natural-language answer; show SQL |
| 3g Langfuse observability | pending | none | traces, spans, scores, datasets, experiment comparisons |
| 3h Accuracy experiments | pending | none | A/B test semantic views, entity linking, and few-shot retrieval separately |
| 4a Chat web UI | pending | none | thin frontend over nlq |
| 4b Deploy | pending | none | isolation requirements specified; hosting target pending |

## Phase Order

| Phase | Items | Key Dependency |
|-------|-------|----------------|
| 1 Foundation | 1a–1b | — |
| 2 Ingestion | 2a–2f | 1b schema |
| 3 Text-to-SQL | 3a–3h | 2d data present |
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

## Phase 3 — Text-to-SQL

### Architecture

Use LangChain for model and schema integrations, and LangGraph for branching, shared
state, and the validation/execution retry loop. The model uses an OpenAI-compatible
interface so SEA-LION and GLM can be evaluated without changing pipeline code.

Graph flow:

`question → router → optional entity linking → optional example retrieval → generate SQL → validate → execute → synthesize`

- Router declines strategy, off-topic, and unusable questions in v1.
- Ambiguous entity matches return a clarification question instead of generating SQL.
- Validation or execution errors return to SQL generation with the error in graph state.
- Stop after three generation attempts and report an honest failure.
- Trace every graph execution in Langfuse.

### Build Order

- **3a Offline evaluation**: implement the approved evaluation specification. Freeze and
  identify the SQLite snapshot, create the initial development cases with verified gold
  SQL, and build the deterministic comparator, report format, and fixture-based tests.
  This stage proves scoring without requiring an LLM.
- **3b Model + schema context**: configure LangChain against an OpenAI-compatible
  endpoint (`base_url` + model name). Render only approved analytical tables into the
  schema context; omit raw_runs, sync_state, and sync_log. Include concise column and
  domain descriptions plus reviewed targeted values.
- **3c SQL guardrails**: use sqlglot to require one parseable SELECT statement and
  enforce a table allowlist. Execute only through SQLite's read-only connection with
  a query timeout and result-row cap.
- **3d LangGraph pipeline**: define typed per-question state containing question,
  route, SQL, safe error, attempt count, rows, and status. Add explicit `StateGraph`
  nodes for routing, generation, validation, execution, and retry control. Use fake
  models in automated tests, then complete one required LangGraph Studio walkthrough
  with the configured real model and LangSmith tracing disabled. SEA-LION is the initial
  baseline provider, but the graph remains provider-agnostic.
- **3e End-to-end baseline eval**: connect the real-model LangGraph pipeline to the
  Phase 3a offline evaluator. Run every case through three independent, cache-disabled
  trials and report execution accuracy, case-level score, first-attempt accuracy, and
  retry recovery.
- **3f Answer synthesis**: turn successful result rows into a concise answer and return
  the generated SQL for transparency. Core v1 evals grade rows, not prose; answer
  faithfulness judging is optional later work.
- **3g Langfuse observability**: record one trace per question and spans for graph nodes,
  including prompts, model outputs, timing, token usage, errors, and execution-accuracy
  scores. Group full eval sweeps as named experiments for before/after comparison.
- **3h Accuracy experiments**: start with a measurable baseline, then test semantic SQL
  views, entity linking, and few-shot retrieval separately. Change one variable per
  experiment while holding the model, dataset, and other settings fixed. Keep additions
  that improve held-out results; record each experiment's accuracy change.

### Evaluation Protocol

- Maintain separate example-library/development data and held-out test questions. A
  held-out question or its gold SQL must never be available to few-shot retrieval or
  prompt optimization.
- Use development questions to choose prompts, thresholds, examples, and graph settings.
  Report final model and A/B results on held-out questions.
- Treat execution accuracy as the primary metric: generated SQL and gold SQL must
  produce equivalent results on the same database snapshot.
- The result comparator must support scalar aggregates, tabular results, float
  tolerances, order-sensitive rankings, and order-insensitive result sets.
- Record valid-SQL rate, execution-success rate, router accuracy, first-attempt
  accuracy, retry recovery rate, soft result F1, latency, token usage, and guardrail
  rejection rates as secondary metrics.
- Tag questions by tables and concepts. Quantify failures by category: parse error,
  invented table, invented column, wrong join, missing filter, wrong aggregation,
  missing DISTINCT, timeout, and result mismatch.
- Use Langfuse experiment names to compare baseline and one-variable variants. Store
  prompts, model settings, dataset version, and aggregate/per-tag scores with each run.

### Entity Linking

- Map user mentions such as `gold axe` to canonical database entities such as
  `cards.card_id = 'GOLD_AXE'` before SQL generation.
- Use deterministic normalization, exact name matching, reviewed aliases, then
  RapidFuzz similarity. RapidFuzz scores are similarity values, not probabilities.
- Accept a fuzzy match only when it clears both a minimum score and a sufficient margin
  over the second candidate. Tune both settings on development examples.
- If multiple candidates remain close, return their names and relevant metadata for
  clarification. Do not let the LLM silently choose.

### Few-Shot Example Library

- Store curated, manually verified question-SQL pairs separately from the held-out
  evaluation set. Tag examples by tables, joins, filters, aggregates, and known schema
  pitfalls.
- In production, retrieve a small number of examples relevant to the current question
  and insert them into the SQL-generation prompt. Begin with deterministic tag/keyword
  selection; evaluate embedding-based selection only if it improves held-out accuracy.
- Run the same retrieval component during offline evaluation. Compare no examples,
  static examples, and dynamic retrieval as separate A/B experiments.
- Promote corrected production questions into the library only after human review and
  SQL verification.

### Optional Optimization and External Benchmarks

- After the LangChain/LangGraph baseline is measured, DSPy may optimize the SQL
  generator's instructions and few-shot examples against execution accuracy. DSPy is
  an offline optimizer for that node; it does not replace LangGraph, SQL guardrails, or
  system-level A/B experiments. Expand and split the dataset before reporting DSPy
  results so optimization examples remain separate from held-out tests.
- Public benchmarks such as BIRD, Spider 2.0, and LiveSQLBench may provide an external
  SEA-LION-vs-GLM comparison. They are optional and do not block the application. The
  StS2 held-out eval remains the model-selection source of truth for this project.

### Guardrails

1. Dedicated router for stats vs strategy/off-topic questions.
2. Treat the user's question as input data, not pipeline instructions.
3. sqlglot parsing, one-statement enforcement, and SELECT-only validation.
4. Deterministic table allowlist shared by schema rendering and validation.
5. Read-only SQLite connection, execution timeout, and output row cap.
6. Maximum three attempts, followed by an explicit failure response.

**Verification (phase):** all guardrail tests pass; baseline and improved eval
experiments are recorded with execution accuracy broken down by question tags.

## Phase 4 — Web UI

- **4a Chat web UI**: thin frontend over nlq pipeline.
- **4b Deploy**: host the backend in a restricted container running as a non-root
  user. Mount application code and the analytical database read-only; expose no
  Docker socket or unrelated host directories; provide only a small temporary
  filesystem; restrict outbound network access to required model-provider endpoints;
  apply CPU, memory, request-size, query-time, and rate limits; store secrets outside
  the image; and place the service behind an authenticated reverse proxy or tunnel.

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
