# Phase 3e DuckDB Analytics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the frozen SQLite analytical snapshot with a verified, restricted native DuckDB snapshot while keeping SQLite as the raw/control store.

**Architecture:** A trusted maintenance command copies the six approved analytical tables from the frozen SQLite source into a versioned DuckDB file. Runtime schema inspection, SQL validation, offline evaluation, and LangGraph execution then use DuckDB through engine-neutral interfaces; ingestion remains unchanged.

**Tech Stack:** Python 3.13+, uv, DuckDB 1.5.5+, SQLGlot, LangChain, LangGraph, pytest.

**Spec:** `docs/specs/phase-3e-duckdb-analytics.md`

## Global Constraints

- Preserve the six analytical table names, columns, values, and primary keys.
- Keep `raw_runs`, `sync_state`, and `sync_log` in SQLite and hidden from NLQ.
- Do not change API fetching, incremental sync, entity keys, or LangGraph topology.
- Use native DuckDB storage for runtime analytical queries.
- Open runtime DuckDB connections read-only with `memory_limit = "4GB"` and `threads = 4`.
- Keep the SQL deadline at 10 seconds and retain row and byte result limits.
- Disable external access, extension installation/autoloading, community extensions, and unsigned extensions before locking runtime configuration.
- Test fixtures and the automated suite must not download extensions or use the network.
- Never modify the frozen SQLite source or publish a partially verified DuckDB target.
- Stop after every task for human review before committing or continuing.
- Tasks 3–5 are vertical slices: the DuckDB manifest, schema context, and offline
  evaluator; guarded LangGraph execution; then snapshot publication and acceptance.
- Run focused tests while building each slice, then require the full suite once at
  the end of that complete slice. Do not add temporary SQLite compatibility code
  solely to keep intermediate partial slices green.

---

### Task 1: DuckDB Dependency and Runtime Settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `config.toml`
- Modify: `nlq/pipeline_settings.py`
- Modify: `tests/nlq/test_pipeline_settings.py`

**Interfaces:**
- Consumes: `[nlq]` values from `config.toml`.
- Produces: `PipelineSettings(max_attempts, query_timeout_seconds, duckdb_memory_limit, duckdb_threads)`.

- [x] **Step 1: Write failing settings tests**

Add tests proving valid DuckDB settings load and invalid values fail:

```python
def test_loads_duckdb_runtime_settings(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[nlq]\n"
        "max_attempts = 3\n"
        "query_timeout_seconds = 10.0\n"
        'duckdb_memory_limit = "4GB"\n'
        "duckdb_threads = 4\n"
    )

    settings = load_pipeline_settings(path)

    assert settings.duckdb_memory_limit == "4GB"
    assert settings.duckdb_threads == 4


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "4"])
def test_rejects_invalid_duckdb_thread_count(tmp_path, value):
    path = write_config(tmp_path, duckdb_threads=value)
    with pytest.raises(ValueError, match="nlq.duckdb_threads"):
        load_pipeline_settings(path)


@pytest.mark.parametrize("value", ["", "   ", 4, None])
def test_rejects_invalid_duckdb_memory_limit(tmp_path, value):
    path = write_config(tmp_path, duckdb_memory_limit=value)
    with pytest.raises(ValueError, match="nlq.duckdb_memory_limit"):
        load_pipeline_settings(path)
```

- [x] **Step 2: Run the focused tests and confirm failure**

Run: `uv run pytest tests/nlq/test_pipeline_settings.py -q`

Expected: failures because `PipelineSettings` has no DuckDB fields.

- [x] **Step 3: Add DuckDB and settings**

Add `duckdb>=1.5.5` through uv. Extend the settings type and loader:

```python
@dataclass(frozen=True)
class PipelineSettings:
    max_attempts: int
    query_timeout_seconds: float
    duckdb_memory_limit: str
    duckdb_threads: int
```

Validate the memory limit as a non-empty string and thread count as a positive integer. Add:

```toml
duckdb_memory_limit = "4GB"
duckdb_threads = 4
```

Do not read these values from `.env`.

- [x] **Step 4: Run focused and full tests**

Run: `uv run pytest tests/nlq/test_pipeline_settings.py -q`

Expected: all settings tests pass.

Run: `uv run pytest -q`

Expected: the complete suite passes.

- [x] **Step 5: Review and commit**

Proposed commit: `build: add DuckDB runtime settings`

Stop for review before committing.

---

### Task 2: Explicit DuckDB Schema and Snapshot Creator

**Files:**
- Create: `duckdb_analytics_schema.sql`
- Modify: `eval/snapshot.py`
- Modify: `eval/__main__.py`
- Modify: `tests/eval/test_snapshot.py`
- Modify: `tests/eval/test_cli.py`

**Interfaces:**
- Consumes: frozen SQLite source path, DuckDB output path, schema path, optional project symlink path.
- Produces: `create_snapshot(source: Path, output: Path, schema: Path, link: Path | None = None) -> dict[str, int]`.
- Produces: a verified native DuckDB file containing `ANALYTICAL_TABLES` only.

- [x] **Step 1: Write failing native-schema tests**

Create tests using small temporary DuckDB files only:

```python
def test_schema_creates_only_approved_tables(tmp_path):
    database = tmp_path / "snapshot.duckdb"
    apply_duckdb_schema(database, Path("duckdb_analytics_schema.sql"))

    with duckdb.connect(str(database), read_only=True) as connection:
        names = {
            row[0]
            for row in connection.execute("SHOW TABLES").fetchall()
        }

    assert names == set(ANALYTICAL_TABLES)


def test_duckdb_schema_preserves_primary_keys(tmp_path):
    database = tmp_path / "snapshot.duckdb"
    apply_duckdb_schema(database, Path("duckdb_analytics_schema.sql"))
    assert primary_key_columns(database) == {
        "runs": ("run_id",),
        "cards": ("card_id",),
        "relics": ("relic_id",),
    }
```

Add lifecycle tests proving existing output and temporary files are rejected, failures remove only the temporary target, and the symlink is updated only after verification.

- [x] **Step 2: Run the snapshot tests and confirm failure**

Run: `uv run pytest tests/eval/test_snapshot.py tests/eval/test_cli.py -q`

Expected: failures because the schema and DuckDB snapshot API do not exist.

- [x] **Step 3: Add the explicit DuckDB schema**

Translate the six current SQLite tables to DuckDB without redesigning them:

```sql
CREATE TABLE cards (
    card_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    description VARCHAR,
    cost INTEGER,
    is_x_cost INTEGER,
    type VARCHAR,
    rarity VARCHAR,
    color VARCHAR,
    target VARCHAR,
    keywords VARCHAR
);
```

Define all remaining columns in the same order as `schema.sql`. Use `VARCHAR` for current text representations and `INTEGER` for current integer/boolean-like representations. Do not add analytical indexes yet.

- [x] **Step 4: Implement trusted snapshot creation**

Keep trusted migration SQL separate from generated SQL. Structure `eval/snapshot.py` around these helpers:

```python
ANALYTICAL_TABLES = (
    "cards",
    "relics",
    "runs",
    "run_cards",
    "run_relics",
    "run_card_choices",
)


def create_snapshot(
    source: Path,
    output: Path,
    schema: Path,
    link: Path | None = None,
) -> dict[str, int]: ...


def apply_duckdb_schema(database: Path, schema: Path) -> None: ...


def _attach_sqlite_source(connection, source: Path) -> None:
    connection.execute("INSTALL sqlite")
    connection.execute("LOAD sqlite")
    connection.execute(
        "ATTACH ? AS source_sqlite (TYPE sqlite, READ_ONLY)",
        [str(source.resolve())],
    )


def _copy_approved_tables(connection) -> None:
    for table in ANALYTICAL_TABLES:
        connection.execute(
            f'INSERT INTO "{table}" SELECT * FROM source_sqlite."{table}"'
        )
```

Use `<output>.tmp`, `CHECKPOINT`, close the connection, verify the temporary file, then atomically rename it. Create or replace `link` last. Unit-test copy and verification helpers with native temporary DuckDB catalogs; test `_attach_sqlite_source` with a fake connection so automated tests never install an extension.

- [x] **Step 5: Update the snapshot CLI**

Support:

```text
uv run python -m eval snapshot create \
  --source data/spire_eval_2026-07-29.db \
  --output /Volumes/MyVolume/sts2-data/spire_eval_2026-07-29.duckdb \
  --schema duckdb_analytics_schema.sql \
  --link data/spire_eval_2026-07-29.duckdb
```

The CLI prints each copied row count and the final file and link paths.

- [x] **Step 6: Run focused and full tests**

Run: `uv run pytest tests/eval/test_snapshot.py tests/eval/test_cli.py -q`

Expected: all snapshot and CLI tests pass without network access.

Run: `uv run pytest -q`

Expected: the complete suite passes.

- [x] **Step 7: Review and commit**

Proposed commit: `feat: create native DuckDB evaluation snapshots`

Stop for review before committing.

---

### Task 3: DuckDB Manifest, Schema Context, and Offline Evaluator

**Files:**
- Modify: `eval/models.py`
- Modify: `eval/manifest.py`
- Modify: `eval/__main__.py`
- Modify: `tests/eval/test_manifest.py`
- Modify: `tests/eval/test_cli.py`
- Modify: `nlq/schema_context.py`
- Modify: `tests/nlq/conftest.py`
- Modify: `tests/nlq/test_schema_context.py`
- Modify: `tests/nlq/test_schema_cache.py`
- Create: `eval/query_execution.py`
- Delete: `eval/sqlite_eval.py`
- Modify: `eval/run_evaluation.py`
- Create: `tests/eval/test_query_execution.py`
- Delete: `tests/eval/test_sqlite_eval.py`
- Modify: `tests/eval/conftest.py`
- Modify: `tests/eval/test_run_evaluation.py`

**Interfaces:**
- Consumes: verified DuckDB path and frozen SQLite source path.
- Produces: `DatasetManifest` with `engine`, `engine_version`, and `source_database_sha256`.
- Produces: `verify_manifest(manifest, project_root) -> Path` that validates DuckDB metadata and approved tables.
- Consumes: verified DuckDB path, `SchemaDictionary`, and `DatasetManifest`.
- Produces: `inspect_approved_schema(database: Path) -> DatabaseSchema` using DuckDB metadata.
- Preserves: `build_verified_schema_context(...) -> SchemaContext` and its cache behavior.
- Consumes: DuckDB path, gold or predicted SQL, timeout seconds.
- Produces: `execute_query(database: Path, sql: str, timeout_seconds: float) -> QueryResult`.
- Preserves: comparison modes, trial scoring, case-level scoring, tags, and report structure.

- [x] **Step 1: Write failing manifest tests**

Add tests for the required fields and rejection cases:

```python
def test_duckdb_manifest_round_trip(tmp_path):
    manifest = sample_duckdb_manifest(tmp_path)
    output = tmp_path / "manifest.json"

    write_manifest(manifest, output)

    loaded = load_manifest(output)
    assert loaded.engine == "duckdb"
    assert loaded.engine_version == duckdb.__version__
    assert loaded.source_database_sha256 == SOURCE_SHA256


def test_verify_manifest_rejects_wrong_engine(tmp_path): ...


def test_verify_manifest_rejects_missing_approved_table(tmp_path): ...


def test_verify_manifest_rejects_run_count_mismatch(tmp_path): ...
```

- [x] **Step 2: Run manifest tests and confirm failure**

Run: `uv run pytest tests/eval/test_manifest.py tests/eval/test_cli.py -q`

Expected: failures because the manifest model lacks DuckDB metadata.

- [x] **Step 3: Extend the manifest model**

Use this field shape:

```python
@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    engine: str
    engine_version: str
    database: str
    created_at: str
    earliest_run_date: str
    latest_run_date: str
    run_count: int
    database_sha256: str
    source_database_sha256: str
    schema_git_commit: str
```

`create_manifest` calculates both file checksums, reads `duckdb.__version__`, and inspects count/date coverage through a read-only DuckDB connection. `load_manifest` rejects unknown or missing fields. `verify_manifest` checks engine equals `duckdb`, version equals the running DuckDB version, checksum, run count, and all six approved tables.

- [x] **Step 4: Update CLI arguments**

The manifest command accepts `--source-database` in addition to `--database`. It continues to require a project-relative manifest database path so the checked-in manifest points at the symlink rather than an absolute machine-specific SSD path.

- [x] **Step 5: Run focused manifest tests**

Run: `uv run pytest tests/eval/test_manifest.py tests/eval/test_cli.py -q`

Expected: all manifest tests pass.

- [x] **Step 6: Convert fixtures and write failing metadata tests**

Create small native DuckDB fixtures and assert engine-neutral metadata:

```python
def test_preserves_live_duckdb_column_metadata(analytical_database):
    schema = inspect_approved_schema(analytical_database)
    runs = next(table for table in schema.tables if table.name == "runs")

    assert runs.columns[0].name == "run_id"
    assert runs.columns[0].database_type == "VARCHAR"
    assert runs.columns[0].primary_key is True
```

Keep tests proving only `APPROVED_TABLES` appear, table order is stable, dictionary names match, and repeated builds hit the in-memory cache.

- [x] **Step 7: Run schema tests and confirm failure**

Run: `uv run pytest tests/nlq/test_schema_context.py tests/nlq/test_schema_cache.py tests/nlq/test_cli.py -q`

Expected: failures from SQLite-only inspection and `sqlite_type`.

- [x] **Step 8: Replace SQLite metadata inspection**

Rename the column field:

```python
@dataclass(frozen=True)
class ColumnSchema:
    name: str
    database_type: str
    nullable: bool
    primary_key: bool
```

Open DuckDB read-only and inspect each approved table using `duckdb_columns()` or `information_schema.columns`, plus DuckDB constraint metadata for primary keys. Build tables strictly in `APPROVED_TABLES` order. Do not render catalog tables or settings.

Update prompt metadata from SQLite wording to DuckDB wording while preserving the reviewed dictionary descriptions and cache key inputs.

- [x] **Step 9: Run focused schema-context tests**

Run: `uv run pytest tests/nlq/test_schema_context.py tests/nlq/test_schema_cache.py tests/nlq/test_cli.py -q`

Expected: all schema-context tests pass.

- [x] **Step 10: Write failing DuckDB evaluation tests**

Port the executor tests to a native temporary DuckDB database:

```python
def test_executes_query_read_only(sample_database):
    result = execute_query(sample_database, "SELECT COUNT(*) FROM runs", 1.0)
    assert result == QueryResult(column_count=1, rows=((3,),))


def test_reports_invalid_sql(sample_database): ...


def test_rejects_writes_in_read_only_mode(sample_database): ...


def test_interrupts_query_after_timeout(sample_database): ...
```

Update evaluation tests so gold and predicted SQL both execute through the same DuckDB function.

- [x] **Step 11: Run evaluation tests and confirm failure**

Run: `uv run pytest tests/eval -q`

Expected: failures until the engine-neutral module replaces `sqlite_eval`.

- [x] **Step 12: Replace the SQLite-specific evaluator boundary**

Move `QueryExecutionError` and `execute_query` to `eval/query_execution.py`. Use read-only DuckDB and the same timer/interruption pattern as runtime execution, but return every row because deterministic scoring must compare complete results.

Change imports in `eval/run_evaluation.py` and `eval/__main__.py`. Preserve `GoldQueryError`: broken gold SQL remains an eval-data defect, while broken predicted SQL remains a failed trial.

- [x] **Step 13: Run focused and full vertical-slice tests**

Run: `uv run pytest tests/eval tests/nlq/test_schema_context.py tests/nlq/test_schema_cache.py tests/nlq/test_cli.py -q`

Expected: all manifest, schema-context, and evaluator tests pass.

Run: `uv run pytest -q`

Expected: the complete suite passes.

- [ ] **Step 14: Review and commit**

Proposed commit: `feat: move evaluation foundation to DuckDB`

Stop for review before committing.

---

### Task 4: DuckDB Guardrails and LangGraph

**Files:**
- Modify: `nlq/sql_policy.py`
- Modify: `nlq/sql_executor.py`
- Modify: `tests/nlq/test_sql_policy.py`
- Modify: `tests/nlq/test_sql_executor.py`
- Modify: `nlq/text_to_sql_graph.py`
- Modify: `nlq/studio_graph.py`
- Modify: `tests/nlq/test_text_to_sql_graph.py`
- Modify: `tests/nlq/test_studio_graph.py`
- Modify: `tests/nlq/test_text_to_sql_model_output.py` only if prompt assertions live there

**Interfaces:**
- Consumes: untrusted generated SQL, DuckDB path, and `ExecutionLimits`.
- Produces: `validate_sql(sql) -> ValidatedSql` parsed with `read="duckdb"`.
- Produces: `guard_and_execute_sql(...) -> SqlExecutionResult` through a fresh hardened DuckDB connection.
- Consumes: `PipelineSettings` DuckDB fields and the verified DuckDB manifest path.
- Produces: the same `CompiledStateGraph` and `TextToSqlRunResult` interfaces as Phase 3d.
- Preserves: route, generate, validate, execute, retry, and finish nodes and edges.

- [ ] **Step 1: Write failing DuckDB policy tests**

Retain all existing one-query, SELECT-only, table-allowlist, function-allowlist, CTE, size, and AST tests. Add explicit DuckDB attack cases:

```python
@pytest.mark.parametrize(
    ("sql", "category"),
    [
        ("ATTACH 'other.duckdb' AS other", "non_query_statement"),
        ("INSTALL httpfs", "non_query_statement"),
        ("LOAD httpfs", "non_query_statement"),
        ("COPY runs TO '/tmp/runs.csv'", "non_query_statement"),
        ("SET memory_limit = '12GB'", "non_query_statement"),
        ("SELECT * FROM read_csv('/etc/passwd')", "unapproved_table"),
        ("SELECT * FROM sqlite_scan('x.db', 'runs')", "unapproved_table"),
    ],
)
def test_rejects_duckdb_external_operations(sql, category): ...
```

Add executor tests that inspect current settings through a trusted helper before configuration is locked, then prove generated SQL cannot change settings or access files.

- [ ] **Step 2: Run policy and executor tests and confirm failure**

Run: `uv run pytest tests/nlq/test_sql_policy.py tests/nlq/test_sql_executor.py -q`

Expected: failures because parsing and execution still target SQLite.

- [ ] **Step 3: Switch SQLGlot to DuckDB**

Change parser and function rendering to `duckdb`. Remove SQLite-only functions unless DuckDB execution tests prove equivalent behavior. Keep only the minimum reviewed aggregate, scalar, date, string, and window functions needed by the schema and eval cases.

Return the original SQL text in `ValidatedSql`; do not transpile or rewrite model SQL silently.

- [ ] **Step 4: Implement the hardened connection factory**

Extend runtime limits:

```python
@dataclass(frozen=True)
class ExecutionLimits:
    timeout_seconds: float = 10.0
    max_result_rows: int = 200
    max_result_bytes: int = 1_000_000
    duckdb_memory_limit: str = "4GB"
    duckdb_threads: int = 4
```

Open a new connection per execution:

```python
connection = duckdb.connect(
    str(database.resolve()),
    read_only=True,
    config={
        "memory_limit": limits.duckdb_memory_limit,
        "threads": str(limits.duckdb_threads),
        "enable_external_access": "false",
        "autoinstall_known_extensions": "false",
        "autoload_known_extensions": "false",
        "allow_community_extensions": "false",
        "allow_unsigned_extensions": "false",
    },
)
connection.execute("SET lock_configuration = true")
```

Use `threading.Timer` to set a local timeout flag and call `connection.interrupt()`. Classify an interrupted query as `timeout` only when that timer fired; classify other `duckdb.Error` values as `execution_error`. Cancel and join the timer in `finally`, preserve row/byte limits, close the connection, and keep logs free of raw SQL.

- [ ] **Step 5: Run focused guardrail tests**

Run: `uv run pytest tests/nlq/test_sql_policy.py tests/nlq/test_sql_executor.py -q`

Expected: policy, security, timeout, result-limit, and logging tests pass.

- [ ] **Step 6: Write failing prompt and dependency-injection tests**

Assert the generator requests DuckDB SQL and Studio passes all resource settings:

```python
def test_generator_prompt_requests_duckdb_sql(...):
    graph.invoke({"question": "How many runs are stored?"})
    assert "DuckDB" in generator_system_message
    assert "SQLite" not in generator_system_message


def test_studio_graph_injects_duckdb_limits(monkeypatch, tmp_path):
    graph = studio_graph._create_studio_graph(tmp_path)
    assert captured_limits.duckdb_memory_limit == "4GB"
    assert captured_limits.duckdb_threads == 4
    assert captured_limits.timeout_seconds == 10.0
```

- [ ] **Step 7: Run graph tests and confirm failure**

Run: `uv run pytest tests/nlq/test_text_to_sql_graph.py tests/nlq/test_studio_graph.py -q`

Expected: prompt and settings assertions fail against the SQLite configuration.

- [ ] **Step 8: Update prompts and injected limits**

Change model-facing wording to:

```python
ROUTER_SYSTEM_PROMPT = """Classify whether the question can be answered from the supplied statistical database.
..."""

SQL_SYSTEM_PROMPT = """Return exactly one read-only DuckDB SELECT query and no explanation.
..."""
```

`studio_graph.py` resolves the DuckDB path from the verified manifest and creates `ExecutionLimits` with timeout, memory, and thread settings. Do not add new graph nodes or change retry behavior.

- [ ] **Step 9: Run focused and full vertical-slice tests**

Run: `uv run pytest tests/nlq/test_sql_policy.py tests/nlq/test_sql_executor.py tests/nlq/test_text_to_sql_graph.py tests/nlq/test_studio_graph.py -q`

Expected: all guardrail and graph tests pass with fake models.

Run: `uv run pytest -q`

Expected: the complete suite passes.

- [ ] **Step 10: Review and commit**

Proposed commit: `feat: connect guarded LangGraph pipeline to DuckDB`

Stop for review before committing.

---

### Task 5: Publish the Frozen Snapshot and Verify Acceptance

**Files:**
- Modify: `eval/datasets/manifest.json`
- Modify: `ROADMAP.md`
- Modify: `docs/query-performance-benchmarks.md` only if measured values differ
- Modify: `docs/specs/phase-3e-duckdb-analytics.md` only if implementation changes an approved decision
- Create outside Git: `/Volumes/MyVolume/sts2-data/spire_eval_2026-07-29.duckdb`
- Create symlink outside Git tracking: `data/spire_eval_2026-07-29.duckdb`

**Interfaces:**
- Consumes: frozen SQLite snapshot with checksum `0dbc74b8908636de7fb6fa12d0753d6666df61001f175ca3a96a151b78251fb3`.
- Produces: official frozen DuckDB snapshot, symlink, checked-in manifest, and acceptance evidence.

- [ ] **Step 1: Verify the source before migration**

Run:

```text
uv run python -m eval manifest verify --manifest eval/datasets/manifest.json
```

Expected: dataset `sts2-2026-07-29` verifies against the frozen SQLite source before the manifest is replaced.

- [ ] **Step 2: Create the official DuckDB snapshot**

Run the Task 2 command with the external SSD target. This step may install the official DuckDB SQLite extension in the trusted maintenance process and therefore requires explicit network/filesystem approval if the extension is not already cached.

Expected row counts must equal the SQLite source for all six tables. Do not overwrite an existing official target; use a new temporary target and publish only after verification.

- [ ] **Step 3: Create and verify the DuckDB manifest**

Run:

```text
uv run python -m eval manifest create \
  --database data/spire_eval_2026-07-29.duckdb \
  --source-database data/spire_eval_2026-07-29.db \
  --dataset-id sts2-2026-07-29 \
  --schema-git-commit "$(git rev-list -1 HEAD -- duckdb_analytics_schema.sql)" \
  --output eval/datasets/manifest.json

uv run python -m eval manifest verify \
  --manifest eval/datasets/manifest.json
```

Expected: engine, version, both checksums, six tables, run count, and date range verify.

- [ ] **Step 4: Validate all development gold SQL**

Run:

```text
uv run python -m eval cases validate \
  --cases eval/cases/development.json \
  --manifest eval/datasets/manifest.json \
  --timeout-seconds 10
```

Expected: all SQL cases execute successfully and router-only cases are counted without SQL execution. Replace `CAST(win AS REAL)` with `CAST(win AS DOUBLE)` only where DuckDB semantics require it; expected values must remain equivalent.

- [ ] **Step 5: Re-run Q1–Q8 under application limits**

Use native DuckDB with `4GB`, four threads, and a 10-second timeout. Compare complete result hashes with the recorded expected hashes. Record new times only as machine-specific evidence.

Expected: all eight hashes match and every query completes within 10 seconds.

- [ ] **Step 6: Run automated acceptance tests**

Run: `uv run pytest -q`

Expected: the complete suite passes without network access.

- [ ] **Step 7: Run Studio acceptance checks**

Start `uv run langgraph dev`, then verify:

1. A statistical question follows route → generate → validate → execute and succeeds using DuckDB.
2. A strategy question follows route → decline and never executes DuckDB.
3. The rendered graph topology is unchanged.

- [ ] **Step 8: Update roadmap and review the final diff**

Mark Phase 3d done only if its remaining Studio acceptance criteria are now satisfied. Mark Phase 3e done only after every completion criterion in the spec passes. Keep Phase 3f pending.

Run:

```text
git diff --check
git status --short
```

- [ ] **Step 9: Review and commit**

Proposed commit: `docs: record verified DuckDB evaluation snapshot`

Stop for review before committing, merging, or pushing.

---

## Self-Review Results

- Spec coverage: dependency, schema, trusted migration, atomic publication, manifest, schema context, DuckDB dialect, restricted execution, evaluator, LangGraph, benchmarks, and Studio acceptance each map to a task.
- Scope boundary: ingestion and incremental DuckDB updates remain deferred.
- Network boundary: only the manual trusted migration may install/load the official SQLite extension; automated tests use native temporary DuckDB fixtures and fakes.
- Type consistency: runtime execution continues returning `SqlExecutionResult`; offline evaluation continues returning `QueryResult`; graph and report public types remain unchanged.
- Placeholder scan: no implementation placeholders remain; the manifest command resolves the schema commit from Git.

## Execution Mode

Use inline execution in this worktree. Complete one task, show the diff and test evidence, stop for review, then commit only after approval.
