# Phase 3c — SQL Guardrails

## Goal

Treat every SQL string produced by an LLM as untrusted input. Phase 3c validates the
string with SQLGlot, enforces the same policy again through SQLite, and returns bounded
query results or a structured safe error.

Phase 3c is the deterministic security boundary between SQL generation and SQLite. It
does not ask an LLM whether SQL is safe.

## Scope

Phase 3c includes:

- Parse generated SQL with the SQLite dialect.
- Require exactly one read-only query statement.
- Allow only the six analytical tables exposed in Phase 3b.
- Allow only reviewed analytical SQL functions.
- Reject database attachment, pragmas, writes, schema changes, and recursive queries.
- Re-enforce table, function, and operation policy with SQLite's authorizer callback.
- Open SQLite read-only and explicitly disable extension loading.
- Stop queries that exceed a deadline.
- Bound returned rows and result bytes.
- Return structured validation and execution failures for Phase 3d's retry loop.
- Add deterministic tests for valid, invalid, and adversarial SQL.

Phase 3c excludes:

- Calling SEA-LION or another model.
- Generating SQL.
- Routing natural-language questions.
- Retrying failed generation.
- Synthesizing natural-language answers.
- LangGraph and Langfuse integration.
- Container, filesystem, network, and deployment isolation; those belong to Phase 4b.

## Trust Boundary

The input is a plain Python string:

```python
generated_sql = "SELECT character, AVG(win) FROM runs GROUP BY character"
```

The system assumes the string may contain malformed SQL, hallucinated names, multiple
statements, prompt-injected instructions expressed as SQL, or deliberately expensive
queries.

The flow is:

```text
generated SQL string
    -> SQLGlot parse and policy validation
    -> validated query object
    -> restricted SQLite connection
    -> SQLite authorizer callback
    -> deadline and output limits
    -> bounded query result or structured failure
```

SQLGlot and SQLite are independent checks. SQLGlot gives clear failures before database
execution. SQLite remains the final authority on what the connection may do.

## Approach Decision

Three designs were considered:

1. **SQLGlot only:** simplest, but it makes one third-party parser the final security
   boundary and cannot enforce SQLite's actual interpretation.
2. **SQLite authorizer only:** strong database enforcement, but gives poorer feedback,
   does not provide the desired one-statement/query-shape checks, and lets every invalid
   attempt reach SQLite.
3. **Layered validation and authorization:** SQLGlot explains and rejects policy
   violations early; SQLite independently enforces the allowed operations.

Phase 3c uses the third design. Both layers consume the same policy constants so the
extra protection does not create two conflicting allowlists.

## Files

Phase 3c adds:

```text
nlq/sql_policy.py
nlq/sql_executor.py
tests/nlq/test_sql_policy.py
tests/nlq/test_sql_executor.py
```

It updates:

```text
pyproject.toml
uv.lock
ROADMAP.md
```

`eval/sqlite_eval.py` remains the evaluator's trusted gold/prediction executor. Phase
3c does not silently change Phase 3a scoring behavior.

## Shared Policy Constants

The table allowlist has one source of truth. Phase 3c imports the approved table names
already used by Phase 3b schema rendering rather than copying them.

The approved physical tables are:

```text
runs
run_cards
run_relics
run_card_choices
cards
relics
```

The initial execution limits are named constants so tests and later configuration can
refer to them explicitly:

```python
MAX_SQL_CHARACTERS = 20_000
MAX_AST_NODES = 5_000
QUERY_TIMEOUT_SECONDS = 10.0
MAX_RESULT_ROWS = 200
MAX_RESULT_BYTES = 1_000_000
PROGRESS_HANDLER_INTERVAL = 1_000
```

Phase 3d may pass different values through typed settings. Phase 3c validates that all
limits are finite and positive.

## Validation Result

Successful validation returns an immutable object:

```python
@dataclass(frozen=True)
class ValidatedSql:
    sql: str
    tables: tuple[str, ...]
    functions: tuple[str, ...]
```

Only `ValidatedSql` may be passed to the production executor. This makes accidental
execution of an unchecked string harder in normal application code.

## SQLGlot Validation

### Parse Rules

Use `sqlglot.parse(..., read="sqlite")`, not `parse_one`, because the full list is
needed to enforce statement count.

Validation rejects:

- Empty or whitespace-only input.
- Input longer than `MAX_SQL_CHARACTERS`.
- Parser errors.
- Zero or more than one parsed statement.
- A statement whose root is not a query expression.
- Any prohibited operation anywhere in the syntax tree.

A complex query with subqueries, non-recursive CTEs, joins, grouping, window
expressions, or set operations remains one statement and may pass.

### Prohibited Operations

Reject syntax-tree nodes representing:

- `INSERT`, `UPDATE`, `DELETE`, `REPLACE`, or `MERGE`.
- `CREATE`, `ALTER`, `DROP`, or `TRUNCATE`.
- `ATTACH` or `DETACH`.
- `PRAGMA`.
- Transactions, savepoints, commands, or procedural statements.
- Recursive CTEs.

The implementation uses SQLGlot expression classes, not keyword matching. Text inside
a string literal or comment does not determine policy.

### Table Rules

Collect physical table references from the parsed tree.

- Every physical table must be in the shared allowlist.
- Database/catalog qualification is rejected. `main.runs` is unnecessary and is also
  rejected so generated SQL cannot choose a database namespace.
- Table aliases are allowed.
- CTE names are allowed as temporary query names and are not mistaken for physical
  tables.
- The tables `raw_runs`, `sync_state`, `sync_log`, `sqlite_master`, and
  `sqlite_schema` are rejected because they are absent from the allowlist.

### Function Rules

SQL functions are canonicalized to case-insensitive names. The initial allowlist is:

```text
ABS, AVG, COALESCE, COUNT, DATE, DATETIME, DENSE_RANK,
IFNULL, IIF, JULIANDAY, LAG, LEAD, LENGTH, LOWER, LTRIM,
LIKE, MAX, MIN, NULLIF, RANK, REPLACE, ROUND, ROW_NUMBER, RTRIM,
STRFTIME, SUBSTR, SUM, TOTAL, TRIM, UNIXEPOCH, UPPER
```

Normal SQL operators and syntax such as arithmetic, comparisons, `CASE`, `CAST`,
`DISTINCT`, `IN`, and `BETWEEN` remain available. SQLite reports `LIKE` to its
authorizer as a function, so it appears explicitly in the allowlist.

Unknown functions and functions such as `load_extension` and `random` are rejected.
Adding a function later requires one policy change and tests showing its intended use.

## SQLite Enforcement

### Connection

The executor opens the database with a URI containing `mode=ro` and `uri=True`.
Extension loading is explicitly disabled before untrusted SQL is prepared.

The executor does not interpolate user values, paths, or identifiers into additional
SQL. It executes exactly the already validated query string.

### Authorizer Callback

Register a callback with `Connection.set_authorizer()` before preparing the query.
SQLite calls the callback while compiling requested operations.

The callback follows a deny-by-default policy:

- Allow `SQLITE_SELECT`.
- Allow `SQLITE_READ` only for an approved table. Require the database name to be
  `main` when SQLite supplies one; SQLite supplies `None` for some reads such as
  `COUNT(*)`, which is also accepted because the connection has no attached database.
- Allow `SQLITE_FUNCTION` only for an approved function.
- Deny recursive operations.
- Deny every other action code, including writes, DDL, pragmas, attach/detach,
  transactions, and access to temporary or attached databases.

This layer reuses the same table and function allowlists as SQLGlot. It is not a second
independently maintained policy.

The callback records only the first denial as structured metadata and immediately
returns `SQLITE_DENY`. It does not write logs or modify the connection.

### Deadline

Use `Connection.set_progress_handler()` with a monotonic deadline. Once the deadline
passes, the handler returns non-zero and SQLite interrupts the query.

The timeout applies to database execution. SQL length and SQLGlot's
`MAX_AST_NODES` parser limit protect the validation stage separately. The locked
SQLGlot version must support the parser node limit before implementation proceeds.

### Result Limits

Fetch at most `MAX_RESULT_ROWS + 1` rows.

- At or below the limit: return every row with `truncated=False`.
- Above the limit: return the first `MAX_RESULT_ROWS` rows with `truncated=True`.

Track the encoded size of column names and returned cell representations. Abort with a
structured `result_too_large` failure if the result exceeds `MAX_RESULT_BYTES`.

The executor does not rewrite generated SQL by appending `LIMIT`; rewriting can change
query semantics and does not protect intermediate work.

## Execution Result

Successful execution returns:

```python
@dataclass(frozen=True)
class SqlExecutionResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    elapsed_ms: float
    truncated: bool
```

Phase 3d stores this result in graph state. Phase 3e converts it into the evaluator's
existing result representation.

## Errors

Phase 3c defines one public error type carrying a stable category and safe message:

```python
class SqlGuardrailError(Exception):
    category: str
```

Initial categories are:

```text
empty_sql
sql_too_long
parse_error
multiple_statements
non_query_statement
prohibited_operation
unapproved_table
unapproved_function
authorizer_denied
timeout
result_too_large
execution_error
```

Messages may identify a rejected table, function, or SQL construct. They must not
include database filesystem paths, environment variables, API keys, raw exception
chains, or database contents.

Phase 3d may return selected safe messages to the model for correction. It uses the
category for retry policy and observability.

## Logging

The authorizer callback does not log each invocation because SQLite may call it many
times for one query.

The executor writes one structured log after the final outcome:

```text
event=sql_guardrail
outcome=allowed|rejected|failed
category=<category-or-none>
sql_sha256=<hash>
tables=<approved-table-names>
elapsed_ms=<number>
row_count=<number>
truncated=<true-or-false>
```

Normal logs omit the full SQL and user question. Phase 3g may record them in Langfuse
under its separate access and retention policy.

## Prompt-Injection Boundary

Phase 3c does not attempt to determine whether a natural-language question is
malicious. It assumes prompt injection may succeed in influencing SQL generation and
limits what the resulting SQL can do.

For example, a user may ask the model to ignore instructions and read `raw_runs` or
attach a local file. The model may comply, but Phase 3c rejects the generated SQL
deterministically.

The model has no shell, filesystem, arbitrary HTTP, or direct SQLite tool. Phase 3d
passes its generated SQL through this boundary rather than exposing a general-purpose
agent tool.

Infrastructure isolation remains Phase 4b.

## Testing

Tests use temporary SQLite fixtures and no model, API key, network, external SSD, or
production database.

### Accepted Cases

- Simple aggregate over `runs`.
- Join between approved fact and dimension tables.
- Nested subquery.
- Non-recursive CTE.
- `UNION` of two approved queries.
- Window ranking with an approved function.
- Long, formatted, single-statement query.
- Semicolon inside a string literal.
- Table aliases and repeated references.

### Validation Rejections

- Empty SQL.
- Oversized SQL.
- Invalid syntax.
- Two safe `SELECT` statements.
- A safe `SELECT` followed by a write.
- Every prohibited statement family.
- `raw_runs`, `sync_state`, `sync_log`, or an invented table.
- Qualified database names.
- Unknown or unapproved functions.
- Recursive CTE.

### Authorizer Rejections

- Direct execution attempts that bypass normal validator construction in tests.
- Reads from an unapproved table.
- Reads from SQLite metadata.
- Pragmas and attach/detach.
- Writes and schema changes.
- Unapproved function calls.

### Runtime Limits

- Read-only connection rejects writes independently.
- Expensive query is interrupted by the progress handler.
- Exactly the row limit is not truncated.
- More than the row limit is truncated.
- Oversized result fails safely.
- Connection and progress-handler resources close after success and failure.
- One structured log is produced per execution, not per authorizer callback.

## Completion Criteria

Phase 3c is complete when:

- SQLGlot is installed and locked by uv.
- Valid SQL produces `ValidatedSql`.
- Invalid, multi-statement, non-query, unapproved-table, and unapproved-function SQL is
  rejected before execution.
- The SQLite authorizer independently denies every operation outside the policy.
- The database is opened read-only with extension loading disabled.
- Timeout, row, and byte limits are enforced and tested.
- Errors have stable categories and safe messages.
- The full automated suite passes without model or network access.
- `ROADMAP.md` records the final test count and marks Phase 3c done.

Phase 3c does not claim that SEA-LION can generate correct SQL. Phase 3d connects these
guardrails to generation and retries; Phase 3e measures end-to-end accuracy.

## Design References

- SQLGlot parses SQL into expression trees that support table and function inspection:
  <https://github.com/tobymao/sqlglot>
- SQLite recommends an authorizer for SQL received from untrusted sources:
  <https://www.sqlite.org/c3ref/set_authorizer.html>
- SQLite supports read-only URI connections with `mode=ro`:
  <https://www.sqlite.org/uri.html>
- Python exposes SQLite authorizer and progress-handler callbacks:
  <https://docs.python.org/3/library/sqlite3.html>
- OWASP recommends deterministic output validation and least-privilege tool access for
  prompt-injection defence:
  <https://cheatsheetseries.owasp.org/cheatsheets/LLM_Prompt_Injection_Prevention_Cheat_Sheet.html>
