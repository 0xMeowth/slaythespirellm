from pathlib import Path
from threading import Event, Timer

import duckdb

from eval.models import QueryResult


class QueryExecutionError(Exception):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def execute_query(
    database: Path,
    sql: str,
    timeout_seconds: float,
) -> QueryResult:
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    timed_out = Event()
    try:
        with duckdb.connect(str(database.resolve()), read_only=True) as connection:
            timer = Timer(
                timeout_seconds,
                _interrupt_query,
                args=(connection, timed_out),
            )
            timer.start()
            try:
                cursor = connection.execute(sql)
                return QueryResult(
                    column_count=len(cursor.description or ()),
                    rows=tuple(tuple(row) for row in cursor.fetchall()),
                )
            finally:
                timer.cancel()
                timer.join()
    except duckdb.Error as error:
        category = "timeout" if timed_out.is_set() else "execution_error"
        raise QueryExecutionError(category, str(error)) from error


def _interrupt_query(connection: duckdb.DuckDBPyConnection, timed_out: Event) -> None:
    timed_out.set()
    connection.interrupt()
