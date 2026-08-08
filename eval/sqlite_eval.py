import sqlite3
import time
from pathlib import Path

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

    deadline = time.monotonic() + timeout_seconds
    uri = f"file:{database.resolve()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            connection.set_progress_handler(
                lambda: int(time.monotonic() >= deadline),
                1_000,
            )
            cursor = connection.execute(sql)
            return QueryResult(
                column_count=len(cursor.description or ()),
                rows=tuple(tuple(row) for row in cursor.fetchall()),
            )
    except sqlite3.Error as error:
        category = "timeout" if str(error).lower() == "interrupted" else "execution_error"
        raise QueryExecutionError(category, str(error)) from error
