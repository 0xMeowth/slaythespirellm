# ADR 001: Use DuckDB for Analytics

- Status: Accepted
- Date: 2026-09-14

## Context

The text-to-SQL pipeline runs scan-heavy joins and aggregations over up to 45 million
rows. SQLite query latency ranged from 0.468 to 477.869 seconds across eight fixed
questions. DuckDB reading the same SQLite file improved those queries, but native
DuckDB storage was substantially faster.

All three engines returned equivalent results for every benchmark question. Native
DuckDB completed the eight queries in 0.004 to 0.610 seconds and stored the six
analytical tables in 445,132,800 bytes. Full evidence and limitations are recorded in
`docs/query-performance-benchmarks.md`.

## Decision

Use a native DuckDB database for the six tables exposed to analytical SQL:

- `runs`
- `run_cards`
- `run_relics`
- `run_card_choices`
- `cards`
- `relics`

Preserve current table and column names during migration. Do not add integer surrogate
keys, denormalized columns, pre-aggregations, or new indexes without separate evidence.

The storage of `raw_runs`, `sync_state`, and `sync_log` remains a separate decision.
SQLite remains unchanged until DuckDB ingestion, guardrails, evaluation, and snapshot
replacement pass verification.

## Consequences

- Add DuckDB as a project dependency and create a versioned native analytical database.
- Adapt schema inspection, query execution, timeout handling, snapshots, manifests, and
  tests from SQLite to DuckDB.
- Replace SQLite's authorizer with SQLGlot policy checks plus DuckDB read-only and
  external-access restrictions.
- Design sync and serving around DuckDB's single-writer-process constraint.
- Keep benchmark SQL and result comparison as migration acceptance tests.
