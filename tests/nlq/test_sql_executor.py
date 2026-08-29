import pytest

from nlq.sql_executor import execute_validated_sql
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
