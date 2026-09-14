# Phase 3e — Frozen DuckDB Analytics

## Goal

Replace the frozen six-table SQLite analytical snapshot with a native DuckDB snapshot
for local development, offline evaluation, LangGraph testing, and the future demo UI.

Phase 3e changes the analytical database engine without changing the dataset, table
meanings, LangGraph topology, or evaluation questions. Incremental API syncing into
DuckDB is explicitly deferred.

## Evidence and Decision

`docs/query-performance-benchmarks.md` records eight fixed queries executed through
SQLite, DuckDB reading SQLite, and native DuckDB. Every result matched. Native DuckDB
completed the queries in 0.004 to 0.610 seconds under its defaults.

A second run limited DuckDB to 4 GB, four threads, and a 10-second query timeout. All
eight queries completed below one second and produced the expected result hashes.

ADR 001 adopts native DuckDB for the six analytical tables.

## Scope

Phase 3e includes:

- Add DuckDB as a project dependency.
- Define an explicit DuckDB schema for the six analytical tables.
- Create a native DuckDB snapshot from the frozen SQLite snapshot.
- Preserve existing column names and stored value conventions.
- Record DuckDB artifact metadata and checksums in the evaluation manifest.
- Inspect DuckDB metadata when rendering schema context.
- Parse generated SQL using the DuckDB dialect.
- Execute model SQL through a hardened read-only DuckDB connection.
- Adapt offline evaluation to execute gold and predicted SQL through DuckDB.
- Update Phase 3d configuration, fixtures, prompts, and tests for DuckDB.
- Verify row counts, gold-query results, benchmark results, and graph behavior.

Phase 3e excludes:

- Fetching new API runs.
- Incrementally updating DuckDB.
- Cron configuration or snapshot publication automation.
- Moving `raw_runs`, `sync_state`, or `sync_log` out of SQLite.
- Integer surrogate keys, denormalized columns, pre-aggregations, or new indexes.
- Natural-language answer synthesis, Langfuse, and the web UI.

## Storage Boundary

The frozen DuckDB snapshot contains only the tables exposed to analytical SQL:

```text
runs
run_cards
run_relics
run_card_choices
cards
relics
```

SQLite remains the storage format for ingestion-only data:

```text
raw_runs
sync_state
sync_log
```

The current ingestion code remains unchanged in this phase. `raw_runs` preserves the
original API JSON for future re-parsing or a later incremental-sync implementation.

## Frozen Artifact

The migration source is the existing frozen snapshot:

```text
data/spire_eval_2026-07-29.db
```

The native target is stored on the external SSD:

```text
/Volumes/MyVolume/sts2-data/spire_eval_2026-07-29.duckdb
```

The project refers to it through a symlink:

```text
data/spire_eval_2026-07-29.duckdb
```

The existing `spire_q1_benchmark.duckdb` remains a disposable benchmark artifact. It
must not become the official snapshot because it was created manually and lacks the
formal schema, manifest, and repeatable migration path required here.

## Schema

Add an explicit `duckdb_analytics_schema.sql`. Preserve the current columns and their
order so parsing, prompts, gold SQL, and result comparison do not change unnecessarily.

Preserve primary keys on:

```text
runs.run_id
cards.card_id
relics.relic_id
```

Keep current boolean-like values as integers containing `0` or `1`. Keep timestamps and
comma-joined fields in their existing representations. Schema redesign is outside this
engine migration.

Do not reproduce SQLite's single-column analytical indexes initially. The native
benchmarks met the latency target without them. Any future index requires separate
evidence.

## Snapshot Creation

Snapshot creation is trusted maintenance code, separate from model-generated SQL.

The creation flow is:

```text
verify source SQLite manifest and checksum
    -> create temporary DuckDB file
    -> install/load the official SQLite extension in the maintenance process
    -> apply the explicit DuckDB schema
    -> copy the six analytical tables
    -> checkpoint and close DuckDB
    -> verify schema, row counts, and fixed query results
    -> calculate the DuckDB checksum
    -> rename the verified temporary file to its final versioned path
    -> atomically write the updated manifest
    -> create or update the project symlink last
```

The source SQLite snapshot is opened read-only and never modified. A failed migration
must remove only its temporary target and leave both the source snapshot and any prior
verified DuckDB snapshot usable.

## Manifest

The logical dataset ID remains `sts2-2026-07-29` because the rows and date window do not
change. The artifact metadata changes.

The manifest records at least:

| Field | Required value or source |
|---|---|
| `dataset_id` | `sts2-2026-07-29` |
| `engine` | `duckdb` |
| `engine_version` | Version used by the snapshot creator |
| `database` | `data/spire_eval_2026-07-29.duckdb` |
| `created_at` | UTC timestamp generated after verification |
| `earliest_run_date` | `2026-06-01T03:19:17.754000` |
| `latest_run_date` | `2026-07-29T17:08:27.136000` |
| `run_count` | `659515` |
| `database_sha256` | SHA-256 calculated from the verified DuckDB file |
| `source_database_sha256` | `0dbc74b8908636de7fb6fa12d0753d6666df61001f175ca3a96a151b78251fb3` |
| `schema_git_commit` | Commit containing the applied DuckDB schema |

Manifest verification checks the engine, engine version, database path, database
checksum, schema commit, run count, and presence of all six approved tables.

## Schema Context

`nlq/schema_context.py` stops using SQLite URI syntax and `PRAGMA table_info`. It reads
DuckDB metadata and returns the same table, column, nullability, and primary-key concepts
to the renderer.

Rename SQLite-specific internal names such as `sqlite_type` to engine-neutral names
such as `database_type`. Continue to expose only the shared approved-table allowlist.

The reviewed data dictionary remains the source for business meanings, join guidance,
and targeted values. The model never receives `raw_runs`, `sync_state`, `sync_log`,
DuckDB catalog tables, or DuckDB settings tables.

## SQL Dialect

SQLGlot parses generated SQL with the DuckDB dialect. The policy still requires one
read-only query and rejects unapproved tables, functions, database qualification,
attachments, writes, pragmas, settings, extension operations, and recursive queries.

The generation prompt requests DuckDB SQL. Rate calculations use `DOUBLE`; DuckDB's
`REAL` is 32-bit while SQLite's `REAL` behaved as a 64-bit floating-point value.

Development and held-out gold SQL are updated only where dialect semantics differ.
Expected result values remain unchanged.

## Restricted Execution

Every model-generated query passes through one restricted DuckDB connection factory.
The connection is opened read-only and configured before model SQL is executed.

Initial resource limits are:

```toml
[nlq]
query_timeout_seconds = 10
duckdb_memory_limit = "4GB"
duckdb_threads = 4
```

The factory also:

- Disables external file and network access.
- Disables automatic extension installation and loading.
- Disallows community and unsigned extensions.
- Locks configuration before executing model SQL.
- Uses a timer to call DuckDB query interruption at the configured deadline.
- Fetches no more than the configured result-row limit.
- Rejects results above the configured byte limit.
- Closes the connection after execution.

DuckDB settings apply to the running database instance, not the `.duckdb` file. The
factory must therefore apply them whenever the application creates a new instance.

SQLGlot remains the first deterministic policy layer. DuckDB configuration provides
defense in depth. Deployment still requires the operating-system and container
isolation specified in Phase 4b.

## Evaluation

Rename the evaluator's SQLite-specific execution boundary to an engine-neutral name.
Both gold SQL and each generated prediction execute against the same frozen DuckDB
snapshot.

The evaluator continues to compare result values rather than SQL wording. Trial,
case-level, execution-accuracy, first-attempt, and retry-recovery calculations remain
unchanged.

Snapshot and manifest tests use small temporary DuckDB files. Test fixtures must not
download extensions or access the network.

## LangGraph

The Phase 3d graph structure remains:

```text
question
    -> route_question
    -> generate_sql
    -> validate_sql
    -> execute_sql
    -> retry_or_finish
```

Only injected dependencies change: DuckDB schema context, DuckDB SQL validation, and
the DuckDB executor. `TextToSqlState`, routing, attempt counting, correction feedback,
and `TextToSqlRunResult` remain stable.

## Error Handling

Preserve the existing safe error categories where their meaning is unchanged:

```text
parse_error
multiple_statements
non_query_statement
unapproved_table
unapproved_function
timeout
result_too_large
execution_error
```

Do not expose raw DuckDB exceptions to the model or user. Log the internal exception
and return a stable safe message to the graph's correction loop.

Migration failures report the failed table or verification step, preserve the source
snapshot, and never publish a partial target.

## Files

Expected additions include:

```text
duckdb_analytics_schema.sql
docs/specs/phase-3e-duckdb-analytics.md
```

Expected updates include:

```text
pyproject.toml
uv.lock
config.toml
eval/manifest.py
eval/snapshot.py
eval/run_evaluation.py
eval/datasets/manifest.json
nlq/schema_context.py
nlq/sql_policy.py
nlq/sql_executor.py
nlq/text_to_sql_graph.py
nlq/studio_graph.py
tests/eval/
tests/nlq/
ROADMAP.md
```

Exact file renames belong in the implementation plan after the existing callers are
checked.

## Deferred Incremental Sync

Routine DuckDB updates are deferred while the frozen dataset is sufficient for model,
evaluation, and demo development.

The approved future direction is:

```text
copy current full DuckDB snapshot
    + parse only newly downloaded API runs
    + insert that delta into the copy
    + verify the new full snapshot
    + atomically publish it
```

Cursor publication, cross-database failure recovery, cron operation, and API instability
are not Phase 3e completion requirements.

## Completion Criteria

Phase 3e is complete when:

- The official versioned DuckDB snapshot and project symlink exist.
- The manifest verifies the DuckDB file and its SQLite source checksum.
- All six table schemas and row counts match the approved migration rules.
- Q1–Q8 return the recorded expected results under 4 GB and four threads.
- Every development gold query executes successfully against DuckDB.
- SQLGlot and DuckDB security tests reject external access and prohibited operations.
- Query timeout, row limit, and byte limit tests pass.
- The complete automated test suite passes without network access.
- A statistical question succeeds through LangGraph Studio using DuckDB.
- A strategy question follows the decline path without DuckDB execution.
- SQLite remains available as the untouched migration source and raw/control store.
