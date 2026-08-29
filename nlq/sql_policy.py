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
    _reject_recursive_cte(expression)
    tables = _physical_table_names(expression)
    functions = tuple(
        sorted(
            {
                function.sql_name().upper()
                for function in expression.find_all(exp.Func)
            }
        )
    )
    return ValidatedSql(sql=sql, tables=tables, functions=functions)


def _reject_recursive_cte(expression: exp.Query) -> None:
    if any(with_clause.args.get("recursive") for with_clause in expression.find_all(exp.With)):
        raise SqlGuardrailError(
            "prohibited_operation", "recursive queries are not allowed"
        )


def _cte_names(expression: exp.Query) -> set[str]:
    return {cte.alias_or_name.casefold() for cte in expression.find_all(exp.CTE)}


def _physical_table_names(expression: exp.Query) -> tuple[str, ...]:
    cte_names = _cte_names(expression)
    physical_names = {
        table_name
        for table in expression.find_all(exp.Table)
        if (table_name := _validate_table(table, cte_names)) is not None
    }
    return tuple(sorted(physical_names))


def _validate_table(table: exp.Table, cte_names: set[str]) -> str | None:
    name = table.name
    if not table.db and not table.catalog and name.casefold() in cte_names:
        return None
    if table.db or table.catalog:
        qualified_name = ".".join(
            part for part in (table.catalog, table.db, name) if part
        )
        raise SqlGuardrailError(
            "unapproved_table", f"unapproved table: {qualified_name}"
        )
    approved_by_normalized_name = {
        approved.casefold(): approved for approved in APPROVED_TABLES
    }
    approved_name = approved_by_normalized_name.get(name.casefold())
    if approved_name is None:
        raise SqlGuardrailError("unapproved_table", f"unapproved table: {name}")
    return approved_name
