import pytest

from nlq.schema_context import APPROVED_TABLES
from nlq.sql_policy import SqlGuardrailError, ValidatedSql, validate_sql


def test_validates_one_select_statement():
    validated = validate_sql("SELECT COUNT(*) FROM runs")

    assert validated == ValidatedSql(
        sql="SELECT COUNT(*) FROM runs",
        tables=("runs",),
        functions=("COUNT",),
    )


def test_policy_reuses_schema_context_table_allowlist():
    from nlq import sql_policy

    assert sql_policy.APPROVED_TABLES is APPROVED_TABLES


@pytest.mark.parametrize("sql", ["", "   "])
def test_rejects_empty_sql(sql):
    with pytest.raises(SqlGuardrailError, match="SQL is empty") as raised:
        validate_sql(sql)

    assert raised.value.category == "empty_sql"


def test_rejects_oversized_sql():
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql("SELECT 1", max_sql_characters=7)

    assert raised.value.category == "sql_too_long"


def test_rejects_invalid_limit_configuration():
    with pytest.raises(ValueError, match="max_sql_characters must be positive"):
        validate_sql("SELECT 1", max_sql_characters=0)
