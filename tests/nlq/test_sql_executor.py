import hashlib
import logging
import math
import sqlite3

import pytest

from nlq.sql_executor import (
    ExecutionLimits,
    execute_validated_sql,
    guard_and_execute_sql,
)
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


def test_logs_one_success_outcome(analytical_database, caplog):
    with caplog.at_level(logging.INFO, logger="nlq.sql_guardrail"):
        guard_and_execute_sql(analytical_database, "SELECT COUNT(*) FROM runs")

    records = _guardrail_records(caplog)
    assert len(records) == 1
    assert records[0].outcome == "allowed"
    assert records[0].category is None
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

    records = _guardrail_records(caplog)
    assert len(records) == 1
    assert records[0].outcome == "rejected"
    assert records[0].category == "unapproved_table"
    assert secret_literal not in caplog.text
    assert records[0].sql_sha256 == hashlib.sha256(sql.encode()).hexdigest()


def test_logs_one_execution_failure(analytical_database, caplog):
    with sqlite3.connect(analytical_database) as connection:
        connection.execute(
            "INSERT INTO cards(card_id, name) VALUES ('BIG', ?)",
            ("x" * 1_000,),
        )

    with caplog.at_level(logging.INFO, logger="nlq.sql_guardrail"):
        with pytest.raises(SqlGuardrailError):
            guard_and_execute_sql(
                analytical_database,
                "SELECT name FROM cards",
                limits=ExecutionLimits(max_result_bytes=100),
            )

    records = _guardrail_records(caplog)
    assert len(records) == 1
    assert records[0].outcome == "failed"
    assert records[0].category == "result_too_large"


def test_logs_one_authorizer_rejection(analytical_database, caplog, monkeypatch):
    unchecked = ValidatedSql(sql="SELECT * FROM raw_runs", tables=(), functions=())
    monkeypatch.setattr("nlq.sql_executor.validate_sql", lambda sql: unchecked)

    with caplog.at_level(logging.INFO, logger="nlq.sql_guardrail"):
        with pytest.raises(SqlGuardrailError):
            guard_and_execute_sql(analytical_database, unchecked.sql)

    records = _guardrail_records(caplog)
    assert len(records) == 1
    assert records[0].outcome == "rejected"
    assert records[0].category == "authorizer_denied"


@pytest.mark.parametrize(
    "expression",
    [
        "ABS(ascension)",
        "AVG(win)",
        "COALESCE(game_mode, '')",
        "COUNT(*)",
        "DATE(start_time, 'unixepoch')",
        "DATETIME(start_time, 'unixepoch')",
        "DENSE_RANK() OVER (ORDER BY run_time)",
        "IFNULL(game_mode, '')",
        "IIF(win = 1, 'yes', 'no')",
        "JULIANDAY('2026-01-01')",
        "LAG(win) OVER (ORDER BY start_time)",
        "LEAD(win) OVER (ORDER BY start_time)",
        "LENGTH(character)",
        "LOWER(character)",
        "LTRIM(character)",
        "MAX(ascension)",
        "MIN(ascension)",
        "NULLIF(game_mode, '')",
        "RANK() OVER (ORDER BY run_time)",
        "REPLACE(character, 'S', 's')",
        "ROUND(AVG(win), 3)",
        "ROW_NUMBER() OVER (ORDER BY run_time)",
        "RTRIM(character)",
        "STRFTIME('%Y', start_time, 'unixepoch')",
        "SUBSTR(character, 1, 3)",
        "SUM(win)",
        "TOTAL(win)",
        "TRIM(character)",
        "UNIXEPOCH('2026-01-01')",
        "UPPER(character)",
    ],
)
def test_approved_functions_pass_both_policy_layers(analytical_database, expression):
    result = guard_and_execute_sql(
        analytical_database,
        f"SELECT {expression} FROM runs",
    )

    assert len(result.columns) == 1


def test_like_operator_passes_both_policy_layers(analytical_database):
    result = guard_and_execute_sql(
        analytical_database,
        "SELECT character FROM runs WHERE character LIKE 'S%'",
    )

    assert result.columns == ("character",)


def _guardrail_records(caplog):
    return [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "sql_guardrail"
    ]
