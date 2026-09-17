import hashlib
import logging
import math

import duckdb
import pytest

from nlq.sql_executor import (
    ExecutionLimits,
    _open_restricted_connection,
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
        "DELETE FROM runs",
        "DROP TABLE runs",
        "ATTACH ':memory:' AS other",
        "INSTALL httpfs",
        "LOAD httpfs",
        "SET threads = 99",
        "SELECT * FROM read_csv('/etc/passwd')",
    ],
)
def test_hardened_connection_rejects_policy_bypass(analytical_database, sql):
    unchecked = ValidatedSql(sql=sql, tables=(), functions=())

    with pytest.raises(SqlGuardrailError) as raised:
        execute_validated_sql(analytical_database, unchecked)

    assert raised.value.category == "execution_error"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0},
        {"timeout_seconds": math.inf},
        {"max_result_rows": 0},
        {"max_result_bytes": 0},
        {"duckdb_memory_limit": ""},
        {"duckdb_threads": 0},
    ],
)
def test_rejects_invalid_execution_limits(kwargs):
    with pytest.raises(ValueError):
        ExecutionLimits(**kwargs)


def test_marks_result_truncated_above_row_limit(analytical_database):
    with duckdb.connect(str(analytical_database)) as connection:
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
    with duckdb.connect(str(analytical_database)) as connection:
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
    with duckdb.connect(str(analytical_database)) as connection:
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
    with pytest.raises(SqlGuardrailError) as raised:
        execute_validated_sql(
            analytical_database,
            ValidatedSql(
                sql=(
                    "SELECT SUM(a.value * b.value) "
                    "FROM range(1000000000) AS a(value) "
                    "CROSS JOIN range(1000000000) AS b(value)"
                ),
                tables=(),
                functions=(),
            ),
            limits=ExecutionLimits(timeout_seconds=0.001),
        )

    assert raised.value.category == "timeout"


def test_guarded_path_validates_then_executes(analytical_database):
    result = guard_and_execute_sql(
        analytical_database,
        "SELECT COUNT(*) AS run_count FROM runs",
    )

    assert result.columns == ("run_count",)
    assert result.rows == ((0,),)


def test_guarded_path_rejects_before_duckdb(analytical_database):
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
    with duckdb.connect(str(analytical_database)) as connection:
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


def test_logs_one_runtime_rejection(analytical_database, caplog, monkeypatch):
    unchecked = ValidatedSql(sql="SELECT * FROM raw_runs", tables=(), functions=())
    monkeypatch.setattr("nlq.sql_executor.validate_sql", lambda sql: unchecked)

    with caplog.at_level(logging.INFO, logger="nlq.sql_guardrail"):
        with pytest.raises(SqlGuardrailError):
            guard_and_execute_sql(analytical_database, unchecked.sql)

    records = _guardrail_records(caplog)
    assert len(records) == 1
    assert records[0].outcome == "failed"
    assert records[0].category == "execution_error"


def test_restricted_connection_applies_and_locks_settings(analytical_database):
    limits = ExecutionLimits(
        duckdb_memory_limit="1GB",
        duckdb_threads=2,
    )

    with _open_restricted_connection(analytical_database, limits) as connection:
        settings = connection.execute(
            "SELECT current_setting('memory_limit'), "
            "current_setting('threads'), "
            "current_setting('enable_external_access'), "
            "current_setting('autoinstall_known_extensions'), "
            "current_setting('autoload_known_extensions'), "
            "current_setting('allow_community_extensions'), "
            "current_setting('allow_unsigned_extensions'), "
            "current_setting('lock_configuration')"
        ).fetchone()
        with pytest.raises(duckdb.Error):
            connection.execute("SET threads = 3")

    assert settings == ("953.6 MiB", 2, False, False, False, False, False, True)


@pytest.mark.parametrize(
    "expression",
    [
        "ABS(ascension)",
        "AVG(win)",
        "COALESCE(game_mode, '')",
        "COUNT(*)",
        "DENSE_RANK() OVER (ORDER BY run_time)",
        "EPOCH(CURRENT_DATE)",
        "IFNULL(game_mode, '')",
        "JULIAN(CURRENT_DATE)",
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
        "STRFTIME(CURRENT_DATE, '%Y')",
        "SUBSTR(character, 1, 3)",
        "SUM(win)",
        "TRIM(character)",
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
