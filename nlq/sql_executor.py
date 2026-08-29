import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from nlq.schema_context import APPROVED_TABLES
from nlq.sql_policy import APPROVED_FUNCTIONS, SqlGuardrailError, ValidatedSql


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
    clock: Callable[[], float] = time.monotonic,
) -> SqlExecutionResult:
    started = clock()
    try:
        connection, state = _open_restricted_connection(database)
    except sqlite3.Error:
        raise SqlGuardrailError(
            "execution_error", "SQLite could not open the database"
        ) from None
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
