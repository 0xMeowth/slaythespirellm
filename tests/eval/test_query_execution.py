import duckdb
import pytest

from eval.query_execution import QueryExecutionError, execute_query


def test_executes_query_read_only(sample_database):
    result = execute_query(
        sample_database,
        "SELECT label, value FROM numbers ORDER BY label, value",
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
    with duckdb.connect(str(sample_database), read_only=True) as connection:
        assert connection.execute("SELECT COUNT(*) FROM numbers").fetchone() == (3,)


def test_interrupts_query_after_timeout(sample_database):
    sql = """
        SELECT SUM(left_values.value * right_values.value)
        FROM range(1000000000) AS left_values(value)
        CROSS JOIN range(1000000000) AS right_values(value)
    """

    with pytest.raises(QueryExecutionError) as error:
        execute_query(sample_database, sql, timeout_seconds=0.001)

    assert error.value.category == "timeout"


def test_rejects_non_positive_timeout(sample_database):
    with pytest.raises(ValueError, match="timeout_seconds must be positive"):
        execute_query(sample_database, "SELECT 1", timeout_seconds=0)
