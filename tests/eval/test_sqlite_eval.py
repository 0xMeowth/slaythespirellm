import sqlite3

import pytest

from eval.sqlite_eval import QueryExecutionError, execute_query


def test_executes_select_read_only(sample_database):
    result = execute_query(
        sample_database,
        "SELECT label, value FROM numbers ORDER BY label, rowid",
        timeout_seconds=1.0,
    )

    assert result.column_count == 2
    assert result.rows == (("a", 1.0), ("b", 2.0), ("b", 2.0))


def test_reports_invalid_sql(sample_database):
    with pytest.raises(QueryExecutionError) as error:
        execute_query(sample_database, "NOT SQL", timeout_seconds=1.0)

    assert error.value.category == "execution_error"


def test_rejects_writes_in_read_only_mode(sample_database):
    with pytest.raises(QueryExecutionError) as error:
        execute_query(
            sample_database,
            "INSERT INTO numbers VALUES ('c', 3.0)",
            timeout_seconds=1.0,
        )

    assert error.value.category == "execution_error"
    with sqlite3.connect(sample_database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM numbers").fetchone() == (3,)


def test_interrupts_query_after_timeout(sample_database):
    sql = """
        WITH RECURSIVE count(value) AS (
            SELECT 1
            UNION ALL
            SELECT value + 1 FROM count
        )
        SELECT SUM(value) FROM count
    """

    with pytest.raises(QueryExecutionError) as error:
        execute_query(sample_database, sql, timeout_seconds=0.001)

    assert error.value.category == "timeout"


def test_rejects_non_positive_timeout(sample_database):
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        execute_query(sample_database, "SELECT 1", timeout_seconds=0)
