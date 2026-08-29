import math
import sqlite3

import pytest

from nlq.sql_executor import ExecutionLimits, execute_validated_sql
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
                "SELECT COUNT(*) FROM runs AS a "
                "CROSS JOIN runs AS b CROSS JOIN runs AS c"
            ),
            limits=ExecutionLimits(
                timeout_seconds=1.0,
                progress_handler_interval=1,
            ),
            clock=lambda: next(ticks, 2.0),
        )

    assert raised.value.category == "timeout"
