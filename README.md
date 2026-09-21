# Slay the Spire 2 — Text-to-SQL

Ask questions in English about *Slay the Spire 2* community run data and get answers
backed by validated, sandboxed SQL. The language model translates a question into a
single read-only query; it never touches the game and never executes anything the
guardrail layer has not checked first.

Data comes from the community Spire Codex API: ~660k runs plus the card and relic
reference set, ingested into DuckDB.

> **Status: work in progress.** The pipeline runs end to end against a real model, and
> the guardrail and execution layers are complete and tested. The accuracy evaluation,
> answer synthesis, and web UI are not built yet. See [Status](#status) for the exact
> line between done and not done.

## What works today

- **Ingestion.** 659,515 runs (2026-06-01 → 07-29) and the full card/relic reference set
  pulled from the Spire Codex API into DuckDB, with cursor pagination, 429 backoff, and
  content-hash deduplication (the export itself repeats ~30% of lines).
- **Text-to-SQL agent.** A LangGraph `StateGraph` with typed per-question state:
  `route → generate SQL → validate → execute → retry`. The router declines strategy,
  advice, and off-topic questions. Generation, validation, and execution errors feed
  back into a bounded retry loop (max 3 attempts), then the graph reports an honest
  failure instead of guessing.
- **The question is data, not instructions.** It is passed as a labelled `HumanMessage`,
  never concatenated into the system prompt — so a question cannot rewrite the model's
  instructions.
- **SQL guardrails** (`nlq/sql_policy.py`). Every generated query must parse to exactly
  one `SELECT` (sqlglot). Rejected: multiple statements, any DML/DDL, tables outside a
  6-table allowlist, functions outside a ~28-function allowlist, database-qualified
  names, oversized queries (character and AST-node caps).
- **Hardened execution** (`nlq/sql_executor.py`). Queries run only on a read-only DuckDB
  connection with external access disabled, extension autoloading and community/unsigned
  extensions disabled, `lock_configuration = true`, and per-query timeout, memory, thread,
  result-row and result-byte limits.
- **Provider-agnostic model client.** OpenAI-compatible interface (`base_url` + model),
  so any compatible endpoint works without pipeline changes. The baseline is
  [SEA-LION](https://sea-lion.ai) (`aisingapore/Qwen-SEA-LION-v4.5-27B-IT`).
- **Deterministic offline evaluation harness.** A frozen DuckDB snapshot, gold-SQL
  development cases, and a row-comparison scorer that runs without an LLM.
- **388 passing tests**, including an adversarial guardrail matrix
  (`test_rejects_adversarial_sql`, `test_rejects_duckdb_external_operations`,
  `test_dangerous_words_inside_literal_do_not_change_policy`,
  `test_hardened_connection_rejects_policy_bypass`, and more).

## Architecture

```
question
  → router            (SQL-answerable? else decline)
  → generate SQL      (model, schema context only)
  → validate          (sqlglot: single SELECT, table/function allowlists, size caps)
  → execute           (read-only sandboxed DuckDB, resource + result limits)
  → retry on error    (feed error back to generate, max 3 attempts)
```

- **Model / schema** — `langchain-openai` `ChatOpenAI` against an OpenAI-compatible
  endpoint. Only the 6 approved analytical tables are rendered into schema context;
  raw and bookkeeping tables are withheld from the model.
- **Graph** — LangGraph `StateGraph` with typed state (question, route, SQL, safe error,
  attempt count, rows, status). Tests use fake models; a real-model walkthrough runs in
  LangGraph Studio.
- **Guardrails / execution** — two independent layers: static policy (sqlglot) before
  the query ever reaches DuckDB, then a hardened read-only connection.

## Run it

Requires Python 3.13+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync

# ingest reference + run data into DuckDB (see ingest/ for options)
uv run python -m ingest.sync_card_relic_data
uv run python -m ingest.sync_runs_data

# run the test suite
uv run pytest -q
```

Configure the model endpoint in `config.toml` / model settings (`base_url`, model name,
API key via environment). Execution and retry limits live in `config.toml`.

## Status

| Area | State |
|------|-------|
| Ingestion (reference + ~660k runs) | Done |
| Incremental sync + cron | Sync done; cron entry pending |
| LangGraph pipeline (route → generate → validate → execute → retry) | Done |
| SQL guardrails + hardened DuckDB execution | Done |
| Offline evaluation harness (frozen snapshot, deterministic scorer) | Done; 10 development cases, held-out set to expand to 40 |
| End-to-end accuracy evaluation | **Not started** — real-model pipeline not yet scored on the eval set |
| Answer synthesis (rows → natural-language answer) | **Not started** |
| Observability (Langfuse traces) | **Not started** |
| Accuracy experiments (views, entity linking, few-shot) | **Not started** |
| Web UI | **Not started** |

No accuracy numbers are reported because the end-to-end evaluation has not run. The
harness to produce them exists; connecting it to the live pipeline is the next step.

## Layout

```
ingest/   Spire Codex API → DuckDB (fetch, parse, dedup, sync)
nlq/      text-to-SQL: graph, guardrails, executor, schema context, model client
eval/     offline evaluation: frozen snapshot, cases, comparator, reports
tests/    388 tests, including the adversarial guardrail matrix
```

## Notes

Personal project. Data is community-contributed run data from the Spire Codex API, not
official Mega Crit data. `aisingapore/Qwen-SEA-LION-v4.5-27B-IT` is used under its own
license.
