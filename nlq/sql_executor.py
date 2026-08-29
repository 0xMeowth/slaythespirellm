import math
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nlq.schema_context import APPROVED_TABLES
from nlq.sql_policy import APPROVED_FUNCTIONS, SqlGuardrailError, ValidatedSql


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


@dataclass(frozen=True)
class AuthorizationDenial:
    action: int
    object_name: str | None
    database_name: str | None


@dataclass
class _AuthorizerState:
    denial: AuthorizationDenial | None = None


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
    limits: ExecutionLimits = ExecutionLimits(),
    clock: Callable[[], float] = time.monotonic,
) -> SqlExecutionResult:
    started = clock()
    deadline = started + limits.timeout_seconds
    try:
        connection, state = _open_restricted_connection(database)
    except sqlite3.Error:
        raise SqlGuardrailError(
            "execution_error", "SQLite could not open the database"
        ) from None
    try:
        connection.set_progress_handler(
            lambda: int(clock() >= deadline),
            limits.progress_handler_interval,
        )
        cursor = connection.execute(validated_sql.sql)
        columns = tuple(column[0] for column in (cursor.description or ()))
        fetched_rows = cursor.fetchmany(limits.max_result_rows + 1)
        truncated = len(fetched_rows) > limits.max_result_rows
        rows = tuple(tuple(row) for row in fetched_rows[: limits.max_result_rows])
        if _result_size_bytes(columns, rows) > limits.max_result_bytes:
            raise SqlGuardrailError(
                "result_too_large", "SQL result exceeds the byte limit"
            )
    except sqlite3.Error as error:
        if state.denial is not None:
            raise SqlGuardrailError(
                "authorizer_denied", "SQLite authorizer denied the query"
            ) from None
        if str(error).casefold() == "interrupted":
            raise SqlGuardrailError("timeout", "SQLite query timed out") from None
        raise SqlGuardrailError(
            "execution_error", "SQLite could not execute the query"
        ) from None
    finally:
        connection.close()
    return SqlExecutionResult(
        columns=columns,
        rows=rows,
        elapsed_ms=(clock() - started) * 1000,
        truncated=truncated,
    )


def _open_restricted_connection(
    database: Path,
) -> tuple[sqlite3.Connection, _AuthorizerState]:
    uri = f"{database.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.enable_load_extension(False)
    state = _AuthorizerState()
    connection.set_authorizer(_build_authorizer(state))
    return connection, state


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


def _result_size_bytes(
    columns: tuple[str, ...], rows: tuple[tuple[object, ...], ...]
) -> int:
    column_bytes = sum(len(column.encode("utf-8")) for column in columns)
    cell_bytes = sum(
        len(repr(cell).encode("utf-8")) for row in rows for cell in row
    )
    return column_bytes + cell_bytes
