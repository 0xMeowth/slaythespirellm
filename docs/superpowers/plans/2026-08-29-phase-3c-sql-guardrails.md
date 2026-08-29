# Phase 3c SQL Guardrails Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic boundary that validates LLM-generated SQL, enforces the same policy inside SQLite, and returns bounded results or safe structured failures.

**Architecture:** `nlq/sql_policy.py` owns SQLGlot parsing, policy constants, validation types, and validation errors. `nlq/sql_executor.py` owns SQLite authorization, read-only execution, deadlines, output limits, and one structured outcome log. The executor accepts only `ValidatedSql`; `guard_and_execute_sql()` is the public convenience path that validates and executes one untrusted string.

**Tech Stack:** Python 3.13+, SQLGlot with SQLite dialect, Python `sqlite3`, `dataclasses`, `logging`, `pytest`, uv.

**Spec:** `docs/specs/phase-3c-sql-guardrails.md`

## Global Constraints

- Treat every model-generated SQL string as untrusted.
- Reuse `nlq.schema_context.APPROVED_TABLES`; do not duplicate the table allowlist.
- Permit exactly one read-only query statement.
- Reject recursive CTEs, database qualification, pragmas, attachment, writes, and DDL.
- Open SQLite with `mode=ro`, disable extensions, and install an authorizer before preparation.
- Default limits: 20,000 SQL characters, 5,000 AST nodes, 10 seconds, 200 rows, and 1,000,000 result bytes.
- Automated tests use temporary databases, no model, no API key, no network, and no external SSD.
- Do not change `eval/sqlite_eval.py`; Phase 3e performs evaluator integration.
- Do not implement LangGraph, retry logic, answer synthesis, Langfuse, or deployment isolation.
- Follow test-driven development and commit after each completed task.

---

## File Structure

- `nlq/sql_policy.py`: shared policy constants, `ValidatedSql`, `SqlGuardrailError`, SQLGlot parsing, AST inspection, table/function validation.
- `nlq/sql_executor.py`: execution settings/result types, SQLite authorizer, read-only connection, deadline, row/byte limits, structured logs, public guarded execution.
- `tests/nlq/test_sql_policy.py`: parser, statement, operation, table, function, CTE, and complexity tests.
- `tests/nlq/test_sql_executor.py`: authorizer, connection, timeout, result-limit, error, and logging tests.
- `pyproject.toml`, `uv.lock`: locked SQLGlot dependency.
- `ROADMAP.md`: final Phase 3c evidence only after all completion checks pass.

---

### Task 1: SQLGlot Dependency and Validation Contract

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `nlq/sql_policy.py`
- Create: `tests/nlq/test_sql_policy.py`

**Interfaces:**
- Consumes: `nlq.schema_context.APPROVED_TABLES: tuple[str, ...]`
- Produces: `ValidatedSql`, `SqlGuardrailError`, `validate_sql(sql: str, *, max_sql_characters: int = MAX_SQL_CHARACTERS, max_ast_nodes: int = MAX_AST_NODES) -> ValidatedSql`

- [ ] **Step 1: Add and lock SQLGlot**

Run:

```bash
uv add sqlglot
```

Expected: `sqlglot` appears in `pyproject.toml`; `uv.lock` resolves successfully.

- [ ] **Step 2: Write failing contract tests**

Create `tests/nlq/test_sql_policy.py` with these initial tests:

```python
import pytest

from nlq.schema_context import APPROVED_TABLES
from nlq.sql_policy import SqlGuardrailError, ValidatedSql, validate_sql


def test_validates_one_select_statement():
    validated = validate_sql("SELECT COUNT(*) FROM runs")

    assert validated == ValidatedSql(
        sql="SELECT COUNT(*) FROM runs",
        tables=("runs",),
        functions=("COUNT",),
    )


def test_policy_reuses_schema_context_table_allowlist():
    from nlq import sql_policy

    assert sql_policy.APPROVED_TABLES is APPROVED_TABLES


@pytest.mark.parametrize("sql", ["", "   "])
def test_rejects_empty_sql(sql):
    with pytest.raises(SqlGuardrailError, match="SQL is empty") as raised:
        validate_sql(sql)

    assert raised.value.category == "empty_sql"


def test_rejects_oversized_sql():
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql("SELECT 1", max_sql_characters=7)

    assert raised.value.category == "sql_too_long"


def test_rejects_invalid_limit_configuration():
    with pytest.raises(ValueError, match="max_sql_characters must be positive"):
        validate_sql("SELECT 1", max_sql_characters=0)
```

- [ ] **Step 3: Run the tests and confirm failure**

Run:

```bash
uv run pytest tests/nlq/test_sql_policy.py -q
```

Expected: FAIL because `nlq.sql_policy` does not exist.

- [ ] **Step 4: Implement the public contract and minimal parser**

Create `nlq/sql_policy.py` with:

```python
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from nlq.schema_context import APPROVED_TABLES


MAX_SQL_CHARACTERS = 20_000
MAX_AST_NODES = 5_000


@dataclass(frozen=True)
class ValidatedSql:
    sql: str
    tables: tuple[str, ...]
    functions: tuple[str, ...]


class SqlGuardrailError(Exception):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def validate_sql(
    sql: str,
    *,
    max_sql_characters: int = MAX_SQL_CHARACTERS,
    max_ast_nodes: int = MAX_AST_NODES,
) -> ValidatedSql:
    if max_sql_characters <= 0:
        raise ValueError("max_sql_characters must be positive")
    if max_ast_nodes <= 0:
        raise ValueError("max_ast_nodes must be positive")
    if not sql.strip():
        raise SqlGuardrailError("empty_sql", "SQL is empty")
    if len(sql) > max_sql_characters:
        raise SqlGuardrailError("sql_too_long", "SQL exceeds the character limit")
    try:
        statements = sqlglot.parse(sql, read="sqlite", max_nodes=max_ast_nodes)
    except ParseError as error:
        raise SqlGuardrailError("parse_error", "SQL could not be parsed") from None
    if len(statements) != 1:
        raise SqlGuardrailError(
            "multiple_statements", "SQL must contain exactly one statement"
        )
    expression = statements[0]
    if not isinstance(expression, exp.Query):
        raise SqlGuardrailError(
            "non_query_statement", "SQL must be a read-only query"
        )
    tables = tuple(sorted({table.name for table in expression.find_all(exp.Table)}))
    functions = tuple(
        sorted({function.sql_name().upper() for function in expression.find_all(exp.Func)})
    )
    return ValidatedSql(sql=sql, tables=tables, functions=functions)
```

If the locked SQLGlot version rejects `max_nodes`, inspect its documented parser API and pass the supported parser option without changing the 5,000-node requirement.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/nlq/test_sql_policy.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock nlq/sql_policy.py tests/nlq/test_sql_policy.py
git commit -m "Add SQL guardrail validation contract"
```

---

### Task 2: Statement, Operation, and Table Policy

**Files:**
- Modify: `nlq/sql_policy.py`
- Modify: `tests/nlq/test_sql_policy.py`

**Interfaces:**
- Consumes: `validate_sql()` and `APPROVED_TABLES` from Task 1.
- Produces: operation-aware and CTE-aware table validation through the same `validate_sql()` interface.

- [ ] **Step 1: Add failing accepted-query tests**

Append tests covering complex single statements:

```python
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT r.character, COUNT(*) FROM runs AS r GROUP BY r.character",
        "SELECT * FROM (SELECT character FROM runs) AS recent_runs",
        "WITH won AS (SELECT run_id FROM runs WHERE win = 1) SELECT COUNT(*) FROM won",
        "SELECT card_id FROM run_cards UNION SELECT card_id FROM cards",
        "SELECT character, RANK() OVER (ORDER BY COUNT(*) DESC) FROM runs GROUP BY character",
        "SELECT ';' AS punctuation FROM runs LIMIT 1",
    ],
)
def test_accepts_complex_single_query(sql):
    assert validate_sql(sql).sql == sql
```

- [ ] **Step 2: Add failing operation and statement-count tests**

```python
@pytest.mark.parametrize(
    ("sql", "category"),
    [
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("SELECT 1; DELETE FROM runs", "multiple_statements"),
        ("INSERT INTO runs(run_id) VALUES ('x')", "non_query_statement"),
        ("UPDATE runs SET win = 1", "non_query_statement"),
        ("DELETE FROM runs", "non_query_statement"),
        ("DROP TABLE runs", "non_query_statement"),
        ("PRAGMA database_list", "non_query_statement"),
        ("ATTACH DATABASE 'other.db' AS other", "non_query_statement"),
    ],
)
def test_rejects_non_query_or_multiple_statement_sql(sql, category):
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql(sql)

    assert raised.value.category == category
```

- [ ] **Step 3: Add failing table and recursive-CTE tests**

```python
@pytest.mark.parametrize(
    "table_name", ["raw_runs", "sync_state", "sync_log", "sqlite_master", "invented"]
)
def test_rejects_unapproved_physical_table(table_name):
    with pytest.raises(SqlGuardrailError, match=table_name) as raised:
        validate_sql(f"SELECT * FROM {table_name}")

    assert raised.value.category == "unapproved_table"


def test_rejects_database_qualification():
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql("SELECT * FROM main.runs")

    assert raised.value.category == "unapproved_table"


def test_does_not_treat_cte_name_as_physical_table():
    validated = validate_sql(
        "WITH won AS (SELECT run_id FROM runs WHERE win = 1) SELECT * FROM won"
    )

    assert validated.tables == ("runs",)


def test_rejects_recursive_cte():
    sql = """
        WITH RECURSIVE counter(n) AS (
            SELECT 1 UNION ALL SELECT n + 1 FROM counter WHERE n < 3
        )
        SELECT * FROM counter
    """
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql(sql)

    assert raised.value.category == "prohibited_operation"
```

- [ ] **Step 4: Run the new tests and confirm failure**

Run:

```bash
uv run pytest tests/nlq/test_sql_policy.py -q
```

Expected: failures for CTE classification, table policy, qualification, and recursion.

- [ ] **Step 5: Implement AST policy helpers**

Add focused helpers named `_reject_recursive_cte`, `_cte_names`,
`_validate_table`, and `_physical_table_names`. Keep each helper private to
`nlq/sql_policy.py` and call them from `validate_sql()` in that order.

Implementation requirements:

- Inspect `exp.With` nodes and reject any whose `recursive` argument is true.
- Collect CTE aliases before evaluating `exp.Table` nodes.
- Ignore a table node only when it is an unqualified reference to a known CTE alias.
- Reject `table.catalog` or `table.db` before checking the physical table name.
- Compare physical names case-insensitively against `APPROVED_TABLES`, then return the canonical allowlist spelling.
- Raise `SqlGuardrailError("unapproved_table", f"unapproved table: {name}")` on failure.
- Preserve sorted unique physical names in `ValidatedSql.tables`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/nlq/test_sql_policy.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nlq/sql_policy.py tests/nlq/test_sql_policy.py
git commit -m "Enforce SQL statement and table policy"
```

---

### Task 3: Function and Parser-Complexity Policy

**Files:**
- Modify: `nlq/sql_policy.py`
- Modify: `tests/nlq/test_sql_policy.py`

**Interfaces:**
- Consumes: SQLGlot expression from `validate_sql()`.
- Produces: `APPROVED_FUNCTIONS: frozenset[str]` and validated canonical function names in `ValidatedSql.functions`.

- [ ] **Step 1: Add failing approved-function tests**

```python
@pytest.mark.parametrize(
    "sql",
    [
        "SELECT AVG(win), COUNT(*), ROUND(AVG(win), 3) FROM runs",
        "SELECT LOWER(name), SUBSTR(name, 1, 3) FROM cards",
        "SELECT character, ROW_NUMBER() OVER (ORDER BY run_time) FROM runs",
        "SELECT name FROM cards WHERE name LIKE '%strike%'",
        "SELECT CASE WHEN win = 1 THEN 'won' ELSE 'lost' END FROM runs",
        "SELECT CAST(ascension AS TEXT) FROM runs",
    ],
)
def test_accepts_approved_functions_and_normal_operators(sql):
    assert validate_sql(sql).sql == sql
```

- [ ] **Step 2: Add failing unapproved-function tests**

```python
@pytest.mark.parametrize("function", ["load_extension", "random", "unknown_func"])
def test_rejects_unapproved_function(function):
    with pytest.raises(SqlGuardrailError, match=function.upper()) as raised:
        validate_sql(f"SELECT {function}() FROM runs")

    assert raised.value.category == "unapproved_function"
```

- [ ] **Step 3: Add failing AST-node-limit test**

```python
def test_rejects_query_exceeding_ast_node_limit():
    sql = "SELECT " + " + ".join("1" for _ in range(100))

    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql(sql, max_ast_nodes=20)

    assert raised.value.category == "sql_too_long"
```

The public category remains `sql_too_long` so callers do not depend on SQLGlot's parser-limit exception wording.

- [ ] **Step 4: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/nlq/test_sql_policy.py -q
```

- [ ] **Step 5: Implement function and node-limit validation**

Define the exact allowlist from the spec:

```python
APPROVED_FUNCTIONS = frozenset(
    {
        "ABS", "AVG", "COALESCE", "COUNT", "DATE", "DATETIME", "DENSE_RANK",
        "IFNULL", "IIF", "JULIANDAY", "LAG", "LEAD", "LENGTH", "LIKE",
        "LOWER", "LTRIM", "MAX", "MIN", "NULLIF", "RANK", "REPLACE",
        "ROUND", "ROW_NUMBER", "RTRIM", "STRFTIME", "SUBSTR", "SUM",
        "TOTAL", "TRIM", "UNIXEPOCH", "UPPER",
    }
)
```

Add:

```python
def _function_name(function: exp.Func) -> str:
    return function.sql_name().upper()


def _validated_functions(expression: exp.Query) -> tuple[str, ...]:
    names = {_function_name(function) for function in expression.find_all(exp.Func)}
    unknown = names - APPROVED_FUNCTIONS
    if unknown:
        raise SqlGuardrailError(
            "unapproved_function",
            f"unapproved function: {sorted(unknown)[0]}",
        )
    return tuple(sorted(names))
```

Map SQLGlot's node-limit exception to `SqlGuardrailError("sql_too_long", "SQL exceeds the syntax-tree limit")`. Do not turn unrelated parser errors into this category.

- [ ] **Step 6: Run policy tests**

Run:

```bash
uv run pytest tests/nlq/test_sql_policy.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nlq/sql_policy.py tests/nlq/test_sql_policy.py
git commit -m "Enforce SQL function and complexity limits"
```

---

### Task 4: SQLite Authorizer and Read-Only Connection

**Files:**
- Create: `nlq/sql_executor.py`
- Create: `tests/nlq/test_sql_executor.py`

**Interfaces:**
- Consumes: `ValidatedSql`, `SqlGuardrailError`, `APPROVED_FUNCTIONS`, and `APPROVED_TABLES`.
- Produces: `AuthorizationDenial`, `_AuthorizerState`, `_build_authorizer(state)`, and `_open_restricted_connection(database: Path)` for executor use.

- [ ] **Step 1: Write failing read-only and authorizer tests**

Create `tests/nlq/test_sql_executor.py`:

```python
import sqlite3

import pytest

from nlq.sql_executor import execute_validated_sql
from nlq.sql_policy import SqlGuardrailError, ValidatedSql, validate_sql


def test_executes_approved_read_query(analytical_database):
    result = execute_validated_sql(
        analytical_database,
        validate_sql("SELECT COUNT(*) AS run_count FROM runs"),
    )

    assert result.columns == ("run_count",)
    assert result.rows == ((0,),)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM raw_runs",
        "SELECT * FROM sqlite_master",
        "PRAGMA database_list",
        "DELETE FROM runs",
        "DROP TABLE runs",
        "ATTACH DATABASE ':memory:' AS other",
        "SELECT load_extension('x')",
    ],
)
def test_authorizer_denies_policy_bypass(analytical_database, sql):
    unchecked = ValidatedSql(sql=sql, tables=(), functions=())

    with pytest.raises(SqlGuardrailError) as raised:
        execute_validated_sql(analytical_database, unchecked)

    assert raised.value.category == "authorizer_denied"
```

- [ ] **Step 2: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/nlq/test_sql_executor.py -q
```

Expected: FAIL because `nlq.sql_executor` does not exist.

- [ ] **Step 3: Implement authorizer state and callback**

Create these types:

```python
@dataclass(frozen=True)
class AuthorizationDenial:
    action: int
    object_name: str | None
    database_name: str | None


@dataclass
class _AuthorizerState:
    denial: AuthorizationDenial | None = None
```

Implement `_build_authorizer(state)` as:

```python
def _build_authorizer(state: _AuthorizerState):
    approved_tables = {table.casefold() for table in APPROVED_TABLES}
    approved_functions = {function.casefold() for function in APPROVED_FUNCTIONS}

    def authorize(action, first, second, database, source):
        allowed = False
        object_name = first
        if action == sqlite3.SQLITE_SELECT:
            allowed = True
        elif action == sqlite3.SQLITE_READ:
            allowed = (
                first is not None
                and first.casefold() in approved_tables
                and database in {None, "main"}
            )
        elif action == sqlite3.SQLITE_FUNCTION:
            object_name = second
            allowed = second is not None and second.casefold() in approved_functions

        if allowed:
            return sqlite3.SQLITE_OK
        if state.denial is None:
            state.denial = AuthorizationDenial(action, object_name, database)
        return sqlite3.SQLITE_DENY

    return authorize
```

This callback:

- Allows `SQLITE_SELECT`.
- Allows `SQLITE_READ` only when the table is approved and the database is `main` or `None`.
- Allows `SQLITE_FUNCTION` only when its function-name argument is approved.
- Denies `SQLITE_RECURSIVE`.
- Denies every other action code.
- Stores only the first denial in `state.denial`.
- Never logs, raises, executes SQL, or modifies the connection.

- [ ] **Step 4: Implement restricted connection opening**

Add `_open_restricted_connection(
database: Path,
) -> tuple[sqlite3.Connection, _AuthorizerState]`:

```python
uri = f"{database.resolve().as_uri()}?mode=ro"
connection = sqlite3.connect(uri, uri=True)
connection.enable_load_extension(False)
state = _AuthorizerState()
connection.set_authorizer(_build_authorizer(state))
return connection, state
```

Keep the authorizer state associated with the connection in the executor's local scope. Close the connection on every success and failure path.

- [ ] **Step 5: Implement minimal execution**

Define temporary public result types now; later tasks add limits and logs:

```python
@dataclass(frozen=True)
class SqlExecutionResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    elapsed_ms: float
    truncated: bool


def execute_validated_sql(
    database: Path,
    validated_sql: ValidatedSql,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> SqlExecutionResult:
    started = clock()
    connection, state = _open_restricted_connection(database)
    try:
        cursor = connection.execute(validated_sql.sql)
        columns = tuple(column[0] for column in (cursor.description or ()))
        rows = tuple(tuple(row) for row in cursor.fetchall())
    except sqlite3.Error:
        if state.denial is not None:
            raise SqlGuardrailError(
                "authorizer_denied", "SQLite authorizer denied the query"
            ) from None
        raise SqlGuardrailError(
            "execution_error", "SQLite could not execute the query"
        ) from None
    finally:
        connection.close()
    return SqlExecutionResult(
        columns=columns,
        rows=rows,
        elapsed_ms=(clock() - started) * 1000,
        truncated=False,
    )
```

When SQLite rejects compilation and `state.denial` is present, raise `SqlGuardrailError("authorizer_denied", "SQLite authorizer denied the query") from None`. For other SQLite failures, raise category `execution_error` with a path-free generic message.

- [ ] **Step 6: Run executor tests**

Run:

```bash
uv run pytest tests/nlq/test_sql_executor.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nlq/sql_executor.py tests/nlq/test_sql_executor.py
git commit -m "Add restricted SQLite query executor"
```

---

### Task 5: Deadline, Row, and Result-Byte Limits

**Files:**
- Modify: `nlq/sql_executor.py`
- Modify: `tests/nlq/test_sql_executor.py`

**Interfaces:**
- Consumes: `execute_validated_sql()` from Task 4.
- Produces: `ExecutionLimits` and bounded `SqlExecutionResult` behavior.

- [ ] **Step 1: Write failing settings-validation tests**

```python
import math

from nlq.sql_executor import ExecutionLimits


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": math.inf},
        {"max_result_rows": 0},
        {"max_result_bytes": 0},
        {"progress_handler_interval": 0},
    ],
)
def test_rejects_invalid_execution_limits(kwargs):
    with pytest.raises(ValueError):
        ExecutionLimits(**kwargs)
```

- [ ] **Step 2: Write failing row and byte-limit tests**

```python
def test_marks_result_truncated_above_row_limit(analytical_database):
    with sqlite3.connect(analytical_database) as connection:
        connection.executemany(
            "INSERT INTO cards(card_id, name) VALUES (?, ?)",
            [(f"CARD_{index}", f"Card {index}") for index in range(4)],
        )

    result = execute_validated_sql(
        analytical_database,
        validate_sql("SELECT card_id FROM cards ORDER BY card_id"),
        limits=ExecutionLimits(max_result_rows=3),
    )

    assert len(result.rows) == 3
    assert result.truncated is True


def test_exact_row_limit_is_not_truncated(analytical_database):
    with sqlite3.connect(analytical_database) as connection:
        connection.executemany(
            "INSERT INTO cards(card_id, name) VALUES (?, ?)",
            [(f"CARD_{index}", f"Card {index}") for index in range(3)],
        )

    result = execute_validated_sql(
        analytical_database,
        validate_sql("SELECT card_id FROM cards ORDER BY card_id"),
        limits=ExecutionLimits(max_result_rows=3),
    )

    assert len(result.rows) == 3
    assert result.truncated is False


def test_rejects_oversized_result(analytical_database):
    with sqlite3.connect(analytical_database) as connection:
        connection.execute(
            "INSERT INTO cards(card_id, name) VALUES ('BIG', ?)",
            ("x" * 1_000,),
        )

    with pytest.raises(SqlGuardrailError) as raised:
        execute_validated_sql(
            analytical_database,
            validate_sql("SELECT name FROM cards"),
            limits=ExecutionLimits(max_result_bytes=100),
        )

    assert raised.value.category == "result_too_large"
```

- [ ] **Step 3: Write a failing deterministic timeout test**

Build a fixture table with enough rows for a cross join and use `progress_handler_interval=1` plus a clock stub whose second call exceeds the deadline:

```python
def test_interrupts_query_after_deadline(analytical_database):
    with sqlite3.connect(analytical_database) as connection:
        connection.executemany(
            """
            INSERT INTO runs(run_id, character, win, was_abandoned, ascension)
            VALUES (?, 'SILENT', 0, 0, 0)
            """,
            [("run-1",), ("run-2",)],
        )

    ticks = iter([0.0, 2.0, 2.0])

    with pytest.raises(SqlGuardrailError) as raised:
        execute_validated_sql(
            analytical_database,
            validate_sql(
                "SELECT COUNT(*) FROM runs AS a CROSS JOIN runs AS b CROSS JOIN runs AS c"
            ),
            limits=ExecutionLimits(
                timeout_seconds=1.0,
                progress_handler_interval=1,
            ),
            clock=lambda: next(ticks, 2.0),
        )

    assert raised.value.category == "timeout"
```

- [ ] **Step 4: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/nlq/test_sql_executor.py -q
```

- [ ] **Step 5: Implement execution limits**

Add:

```python
QUERY_TIMEOUT_SECONDS = 10.0
MAX_RESULT_ROWS = 200
MAX_RESULT_BYTES = 1_000_000
PROGRESS_HANDLER_INTERVAL = 1_000


@dataclass(frozen=True)
class ExecutionLimits:
    timeout_seconds: float = QUERY_TIMEOUT_SECONDS
    max_result_rows: int = MAX_RESULT_ROWS
    max_result_bytes: int = MAX_RESULT_BYTES
    progress_handler_interval: int = PROGRESS_HANDLER_INTERVAL

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if self.max_result_rows <= 0:
            raise ValueError("max_result_rows must be positive")
        if self.max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")
        if self.progress_handler_interval <= 0:
            raise ValueError("progress_handler_interval must be positive")
```

Update the existing signature to add
`limits: ExecutionLimits = ExecutionLimits()` before `clock`, then:

1. Compute the monotonic deadline.
2. Install a progress handler before execution.
3. Execute the validated SQL.
4. Fetch `max_result_rows + 1` rows.
5. Set `truncated` and discard only the sentinel extra row.
6. Count UTF-8 bytes for column names and `repr(cell)` for every returned cell.
7. Raise `result_too_large` if the byte limit is crossed.
8. Translate SQLite's interrupted error to category `timeout`.

- [ ] **Step 6: Run executor tests**

Run:

```bash
uv run pytest tests/nlq/test_sql_executor.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add nlq/sql_executor.py tests/nlq/test_sql_executor.py
git commit -m "Bound SQL execution resources and results"
```

---

### Task 6: Public Guarded Path and Structured Logging

**Files:**
- Modify: `nlq/sql_executor.py`
- Modify: `tests/nlq/test_sql_executor.py`

**Interfaces:**
- Consumes: `validate_sql()` and `execute_validated_sql()`.
- Produces: `guard_and_execute_sql(database: Path, sql: str, *, limits: ExecutionLimits = ExecutionLimits(), logger: logging.Logger = LOGGER, clock: Callable[[], float] = time.monotonic) -> SqlExecutionResult`.

- [ ] **Step 1: Write failing public-path tests**

```python
def test_guarded_path_validates_then_executes(analytical_database):
    result = guard_and_execute_sql(
        analytical_database,
        "SELECT COUNT(*) AS run_count FROM runs",
    )

    assert result.columns == ("run_count",)
    assert result.rows == ((0,),)


def test_guarded_path_rejects_before_sqlite(analytical_database):
    with pytest.raises(SqlGuardrailError) as raised:
        guard_and_execute_sql(analytical_database, "SELECT * FROM raw_runs")

    assert raised.value.category == "unapproved_table"
```

- [ ] **Step 2: Write failing one-log-per-outcome tests**

Use `caplog` and assert custom log-record attributes rather than parsing prose:

```python
def test_logs_one_success_outcome(analytical_database, caplog):
    with caplog.at_level(logging.INFO, logger="nlq.sql_guardrail"):
        guard_and_execute_sql(analytical_database, "SELECT COUNT(*) FROM runs")

    records = [record for record in caplog.records if record.event == "sql_guardrail"]
    assert len(records) == 1
    assert records[0].outcome == "allowed"
    assert records[0].tables == ("runs",)
    assert records[0].row_count == 1


def test_logs_one_validation_rejection_without_full_sql(
    analytical_database, caplog
):
    secret_literal = "do-not-log-this"
    sql = f"SELECT '{secret_literal}' FROM raw_runs"

    with caplog.at_level(logging.INFO, logger="nlq.sql_guardrail"):
        with pytest.raises(SqlGuardrailError):
            guard_and_execute_sql(analytical_database, sql)

    records = [record for record in caplog.records if record.event == "sql_guardrail"]
    assert len(records) == 1
    assert records[0].outcome == "rejected"
    assert records[0].category == "unapproved_table"
    assert secret_literal not in caplog.text
    assert records[0].sql_sha256 == hashlib.sha256(sql.encode()).hexdigest()
```

Add equivalent single-record tests for timeout/result failure and authorizer denial.

- [ ] **Step 3: Run tests and confirm failure**

Run:

```bash
uv run pytest tests/nlq/test_sql_executor.py -q
```

- [ ] **Step 4: Implement the guarded orchestration path**

`guard_and_execute_sql()` must:

1. Hash the SQL before validation.
2. Call `validate_sql()`.
3. Call `execute_validated_sql()` only after validation succeeds.
4. Emit exactly one log in `finally`-equivalent control flow.
5. Classify validation/authorizer denials as `outcome="rejected"`.
6. Classify timeout, result size, and SQLite failures as `outcome="failed"`.
7. Record no full SQL, user question, database path, cell values, or exception traceback.
8. Re-raise the original `SqlGuardrailError` after logging.

Use `logger.info("SQL guardrail outcome", extra=fields)` where `fields` is exactly:

```python
{
    "event": "sql_guardrail",
    "outcome": outcome,
    "category": category,
    "sql_sha256": sql_sha256,
    "tables": tables,
    "elapsed_ms": elapsed_ms,
    "row_count": row_count,
    "truncated": truncated,
}
```

- [ ] **Step 5: Run executor tests**

Run:

```bash
uv run pytest tests/nlq/test_sql_executor.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add nlq/sql_executor.py tests/nlq/test_sql_executor.py
git commit -m "Add guarded SQL execution and outcome logs"
```

---

### Task 7: Adversarial Regression Matrix and Phase Completion

**Files:**
- Modify: `tests/nlq/test_sql_policy.py`
- Modify: `tests/nlq/test_sql_executor.py`
- Modify: `docs/specs/phase-3c-sql-guardrails.md` only if implementation findings require clarification.
- Modify: `ROADMAP.md`

**Interfaces:**
- Consumes: all Phase 3c public interfaces.
- Produces: complete regression evidence and roadmap completion record.

- [ ] **Step 1: Add the adversarial parameter matrix**

Ensure tests explicitly cover:

```python
ADVERSARIAL_SQL = [
    "SELECT * FROM raw_runs",
    "SELECT * FROM sync_state",
    "SELECT * FROM sync_log",
    "SELECT sql FROM sqlite_schema",
    "SELECT * FROM main.runs",
    "SELECT load_extension('/tmp/payload')",
    "PRAGMA database_list",
    "ATTACH DATABASE '/tmp/other.db' AS other",
    "SELECT 1; SELECT 2",
    "SELECT 1; DROP TABLE runs",
    "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) SELECT * FROM x",
]
```

For each case, assert deterministic rejection and a stable category. Separately assert that ordinary quoted text containing words such as `DROP TABLE` remains valid when it is only a string literal.

- [ ] **Step 2: Run Phase 3c tests**

Run:

```bash
uv run pytest tests/nlq/test_sql_policy.py tests/nlq/test_sql_executor.py -q
```

Expected: PASS.

- [ ] **Step 3: Run the full regression suite**

Run:

```bash
uv run pytest -q
```

Expected: all tests from Phase 3a (offline evaluation), Phase 3b, and Phase 3c pass.

- [ ] **Step 4: Run repository checks**

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intentional Phase 3c changes remain.

- [ ] **Step 5: Update Phase 3c evidence**

In `ROADMAP.md`:

- Change Phase 3c status from `in-progress` to `done`.
- Record the final automated test count.
- Record that SQLGlot policy, SQLite authorizer, read-only execution, timeout, row limit, byte limit, and adversarial cases pass.

If implementation changes an approved design detail, update `docs/specs/phase-3c-sql-guardrails.md` in the same commit and explain the reason precisely.

- [ ] **Step 6: Commit completion evidence**

```bash
git add ROADMAP.md docs/specs/phase-3c-sql-guardrails.md tests/nlq/test_sql_policy.py tests/nlq/test_sql_executor.py
git commit -m "Complete Phase 3c SQL guardrails"
```

- [ ] **Step 7: Review before integration**

Run a focused code review against `main` covering:

- Policy bypasses caused by SQLGlot/SQLite interpretation differences.
- Missing SQLite authorizer action codes.
- Secret, path, SQL-text, or row-value leakage in errors and logs.
- Nondeterministic or flaky timeout tests.
- Resource leaks on success and failure.
- Drift between schema rendering and the execution table allowlist.

Do not merge the branch until review findings are resolved and the full suite passes again.
