import hashlib
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Timer

import duckdb

from nlq.sql_policy import (
    SqlGuardrailError,
    ValidatedSql,
    validate_sql,
)


QUERY_TIMEOUT_SECONDS = 10.0
MAX_RESULT_ROWS = 200
MAX_RESULT_BYTES = 1_000_000
DUCKDB_MEMORY_LIMIT = "4GB"
DUCKDB_THREADS = 4
LOGGER = logging.getLogger("nlq.sql_guardrail")
_REJECTION_CATEGORIES = {
    "empty_sql",
    "sql_too_long",
    "parse_error",
    "multiple_statements",
    "non_query_statement",
    "prohibited_operation",
    "unapproved_table",
    "unapproved_function",
}


@dataclass(frozen=True)
class ExecutionLimits:
    timeout_seconds: float = QUERY_TIMEOUT_SECONDS
    max_result_rows: int = MAX_RESULT_ROWS
    max_result_bytes: int = MAX_RESULT_BYTES
    duckdb_memory_limit: str = DUCKDB_MEMORY_LIMIT
    duckdb_threads: int = DUCKDB_THREADS

    def __post_init__(self) -> None:
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if self.max_result_rows <= 0:
            raise ValueError("max_result_rows must be positive")
        if self.max_result_bytes <= 0:
            raise ValueError("max_result_bytes must be positive")
        if not self.duckdb_memory_limit.strip():
            raise ValueError("duckdb_memory_limit must be non-empty")
        if self.duckdb_threads <= 0:
            raise ValueError("duckdb_threads must be positive")


@dataclass(frozen=True)
class SqlExecutionResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[object, ...], ...]
    elapsed_ms: float
    truncated: bool


def guard_and_execute_sql(
    database: Path,
    sql: str,
    *,
    limits: ExecutionLimits = ExecutionLimits(),
    logger: logging.Logger = LOGGER,
    clock: Callable[[], float] = time.monotonic,
) -> SqlExecutionResult:
    started = clock()
    sql_sha256 = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    outcome = "failed"
    category = None
    tables: tuple[str, ...] = ()
    row_count = 0
    truncated = False
    try:
        validated_sql = validate_sql(sql)
        tables = validated_sql.tables
        result = execute_validated_sql(
            database,
            validated_sql,
            limits=limits,
            clock=clock,
        )
        outcome = "allowed"
        row_count = len(result.rows)
        truncated = result.truncated
        return result
    except SqlGuardrailError as error:
        category = error.category
        if category in _REJECTION_CATEGORIES:
            outcome = "rejected"
        raise
    finally:
        logger.info(
            "SQL guardrail outcome",
            extra={
                "event": "sql_guardrail",
                "outcome": outcome,
                "category": category,
                "sql_sha256": sql_sha256,
                "tables": tables,
                "elapsed_ms": (clock() - started) * 1000,
                "row_count": row_count,
                "truncated": truncated,
            },
        )


def execute_validated_sql(
    database: Path,
    validated_sql: ValidatedSql,
    *,
    limits: ExecutionLimits = ExecutionLimits(),
    clock: Callable[[], float] = time.monotonic,
) -> SqlExecutionResult:
    started = clock()
    timed_out = Event()
    try:
        connection = _open_restricted_connection(database, limits)
    except duckdb.Error:
        raise SqlGuardrailError(
            "execution_error", "DuckDB could not open the database"
        ) from None
    timer = Timer(
        limits.timeout_seconds,
        _interrupt_query,
        args=(connection, timed_out),
    )
    try:
        timer.start()
        cursor = connection.execute(validated_sql.sql)
        columns = tuple(column[0] for column in (cursor.description or ()))
        fetched_rows = cursor.fetchmany(limits.max_result_rows + 1)
        truncated = len(fetched_rows) > limits.max_result_rows
        rows = tuple(tuple(row) for row in fetched_rows[: limits.max_result_rows])
        if _result_size_bytes(columns, rows) > limits.max_result_bytes:
            raise SqlGuardrailError(
                "result_too_large", "SQL result exceeds the byte limit"
            )
    except duckdb.Error:
        if timed_out.is_set():
            raise SqlGuardrailError("timeout", "DuckDB query timed out") from None
        raise SqlGuardrailError(
            "execution_error", "DuckDB could not execute the query"
        ) from None
    finally:
        timer.cancel()
        timer.join()
        connection.close()
    return SqlExecutionResult(
        columns=columns,
        rows=rows,
        elapsed_ms=(clock() - started) * 1000,
        truncated=truncated,
    )


def _open_restricted_connection(
    database: Path,
    limits: ExecutionLimits,
) -> duckdb.DuckDBPyConnection:
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
    return connection


def _interrupt_query(connection: duckdb.DuckDBPyConnection, timed_out: Event) -> None:
    timed_out.set()
    connection.interrupt()


def _result_size_bytes(
    columns: tuple[str, ...], rows: tuple[tuple[object, ...], ...]
) -> int:
    column_bytes = sum(len(column.encode("utf-8")) for column in columns)
    cell_bytes = sum(
        len(repr(cell).encode("utf-8")) for row in rows for cell in row
    )
    return column_bytes + cell_bytes
