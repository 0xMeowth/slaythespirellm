from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from nlq.schema_context import APPROVED_TABLES


MAX_SQL_CHARACTERS = 20_000
MAX_AST_NODES = 5_000


@dataclass(frozen=True)
class ValidatedSql:
    sql: str
    tables: tuple[str, ...]
    functions: tuple[str, ...]


class SqlGuardrailError(Exception):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def validate_sql(
    sql: str,
    *,
    max_sql_characters: int = MAX_SQL_CHARACTERS,
    max_ast_nodes: int = MAX_AST_NODES,
) -> ValidatedSql:
    if max_sql_characters <= 0:
        raise ValueError("max_sql_characters must be positive")
    if max_ast_nodes <= 0:
        raise ValueError("max_ast_nodes must be positive")
    if not sql.strip():
        raise SqlGuardrailError("empty_sql", "SQL is empty")
    if len(sql) > max_sql_characters:
        raise SqlGuardrailError("sql_too_long", "SQL exceeds the character limit")
    try:
        statements = sqlglot.parse(sql, read="sqlite", max_nodes=max_ast_nodes)
    except ParseError:
        raise SqlGuardrailError("parse_error", "SQL could not be parsed") from None
    if len(statements) != 1:
        raise SqlGuardrailError(
            "multiple_statements", "SQL must contain exactly one statement"
        )
    expression = statements[0]
    if not isinstance(expression, exp.Query):
        raise SqlGuardrailError(
            "non_query_statement", "SQL must be a read-only query"
        )
    tables = tuple(sorted({table.name for table in expression.find_all(exp.Table)}))
    functions = tuple(
        sorted(
            {
                function.sql_name().upper()
                for function in expression.find_all(exp.Func)
            }
        )
    )
    return ValidatedSql(sql=sql, tables=tables, functions=functions)
