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


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT r.character, COUNT(*) FROM runs AS r GROUP BY r.character",
        "SELECT * FROM (SELECT character FROM runs) AS recent_runs",
        "WITH won AS (SELECT run_id FROM runs WHERE win = 1) SELECT COUNT(*) FROM won",
        "SELECT card_id FROM run_cards UNION SELECT card_id FROM cards",
        "SELECT character, RANK() OVER (ORDER BY COUNT(*) DESC) FROM runs GROUP BY character",
        "SELECT ';' AS punctuation FROM runs LIMIT 1",
    ],
)
def test_accepts_complex_single_query(sql):
    assert validate_sql(sql).sql == sql


@pytest.mark.parametrize(
    ("sql", "category"),
    [
        ("SELECT 1; SELECT 2", "multiple_statements"),
        ("SELECT 1; DELETE FROM runs", "multiple_statements"),
        ("INSERT INTO runs(run_id) VALUES ('x')", "non_query_statement"),
        ("UPDATE runs SET win = 1", "non_query_statement"),
        ("DELETE FROM runs", "non_query_statement"),
        ("DROP TABLE runs", "non_query_statement"),
        ("PRAGMA database_list", "non_query_statement"),
        ("ATTACH DATABASE 'other.db' AS other", "non_query_statement"),
    ],
)
def test_rejects_non_query_or_multiple_statement_sql(sql, category):
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql(sql)

    assert raised.value.category == category


@pytest.mark.parametrize(
    "table_name", ["raw_runs", "sync_state", "sync_log", "sqlite_master", "invented"]
)
def test_rejects_unapproved_physical_table(table_name):
    with pytest.raises(SqlGuardrailError, match=table_name) as raised:
        validate_sql(f"SELECT * FROM {table_name}")

    assert raised.value.category == "unapproved_table"


def test_rejects_database_qualification():
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql("SELECT * FROM main.runs")

    assert raised.value.category == "unapproved_table"


def test_does_not_treat_cte_name_as_physical_table():
    validated = validate_sql(
        "WITH won AS (SELECT run_id FROM runs WHERE win = 1) SELECT * FROM won"
    )

    assert validated.tables == ("runs",)


def test_rejects_recursive_cte():
    sql = """
        WITH RECURSIVE counter(n) AS (
            SELECT 1 UNION ALL SELECT n + 1 FROM counter WHERE n < 3
        )
        SELECT * FROM counter
    """
    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql(sql)

    assert raised.value.category == "prohibited_operation"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT AVG(win), COUNT(*), ROUND(AVG(win), 3) FROM runs",
        "SELECT LOWER(name), SUBSTR(name, 1, 3) FROM cards",
        "SELECT character, ROW_NUMBER() OVER (ORDER BY run_time) FROM runs",
        "SELECT name FROM cards WHERE name LIKE '%strike%'",
        "SELECT CASE WHEN win = 1 THEN 'won' ELSE 'lost' END FROM runs",
        "SELECT CAST(ascension AS TEXT) FROM runs",
    ],
)
def test_accepts_approved_functions_and_normal_operators(sql):
    assert validate_sql(sql).sql == sql


@pytest.mark.parametrize("function", ["load_extension", "random", "unknown_func"])
def test_rejects_unapproved_function(function):
    with pytest.raises(SqlGuardrailError, match=function.upper()) as raised:
        validate_sql(f"SELECT {function}() FROM runs")

    assert raised.value.category == "unapproved_function"


def test_rejects_query_exceeding_ast_node_limit():
    sql = "SELECT " + " + ".join("1" for _ in range(100))

    with pytest.raises(SqlGuardrailError) as raised:
        validate_sql(sql, max_ast_nodes=20)

    assert raised.value.category == "sql_too_long"


def test_rejects_invalid_ast_node_limit():
    with pytest.raises(ValueError, match="max_ast_nodes must be positive"):
        validate_sql("SELECT 1", max_ast_nodes=0)
