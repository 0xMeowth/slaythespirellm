import hashlib
import os
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import duckdb
import pytest

from nlq.schema_context import (
    APPROVED_TABLES,
    DatabaseSchema,
    TableSchema,
    inspect_approved_schema,
    render_schema_context,
    validate_dictionary,
)
from nlq.schema_dictionary import (
    ColumnDescription,
    JoinDescription,
    SchemaDictionary,
    TableDescription,
)


def test_inspects_only_approved_tables(duckdb_analytical_database: Path):
    schema = inspect_approved_schema(duckdb_analytical_database)

    assert tuple(table.name for table in schema.tables) == APPROVED_TABLES
    assert {table.name for table in schema.tables}.isdisjoint(
        {"raw_runs", "sync_state", "sync_log"}
    )


def test_preserves_live_duckdb_column_metadata(duckdb_analytical_database: Path):
    schema = inspect_approved_schema(duckdb_analytical_database)
    runs = schema.tables[0]

    assert tuple(column.name for column in runs.columns[:4]) == (
        "run_id",
        "character",
        "win",
        "was_abandoned",
    )
    assert runs.columns[0].database_type == "VARCHAR"
    assert runs.columns[0].primary_key is True
    assert runs.columns[1].nullable is False


def test_inspection_works_for_read_only_database(duckdb_analytical_database: Path):
    os.chmod(duckdb_analytical_database, 0o444)

    try:
        schema = inspect_approved_schema(duckdb_analytical_database)
    finally:
        os.chmod(duckdb_analytical_database, 0o644)

    assert schema.tables[0].name == "runs"


def test_rejects_missing_approved_table(duckdb_analytical_database: Path):
    with duckdb.connect(str(duckdb_analytical_database)) as connection:
        connection.execute("DROP TABLE relics")

    with pytest.raises(ValueError, match="missing approved table: relics"):
        inspect_approved_schema(duckdb_analytical_database)


def test_rejects_missing_dictionary_table(schema: DatabaseSchema, dictionary: SchemaDictionary):
    missing_table = SchemaDictionary(
        tables=MappingProxyType(
            {name: table for name, table in dictionary.tables.items() if name != "relics"}
        ),
        sha256=dictionary.sha256,
    )

    with pytest.raises(ValueError, match="missing dictionary tables: relics"):
        validate_dictionary(schema, missing_table)


def test_rejects_unknown_dictionary_table(schema: DatabaseSchema, dictionary: SchemaDictionary):
    tables = dict(dictionary.tables)
    tables["unknown"] = TableDescription(
        description="Unknown table.",
        columns=MappingProxyType({"id": ColumnDescription("Identifier.")}),
        joins=(),
    )
    unknown_table = SchemaDictionary(
        tables=MappingProxyType(tables), sha256=dictionary.sha256
    )

    with pytest.raises(ValueError, match="unknown dictionary tables: unknown"):
        validate_dictionary(schema, unknown_table)


@pytest.mark.parametrize(
    "malformed_tables",
    [
        lambda tables: (tables[0], *tables),
        lambda tables: tables[:-1],
        lambda tables: (*tables, TableSchema(name="unknown", columns=())),
        lambda tables: (tables[1], tables[0], *tables[2:]),
    ],
    ids=("duplicate", "missing", "extra", "reordered"),
)
def test_rejects_malformed_database_schema_table_sequence(
    schema: DatabaseSchema, dictionary: SchemaDictionary, malformed_tables
):
    malformed_schema = DatabaseSchema(tables=malformed_tables(schema.tables))

    with pytest.raises(
        ValueError, match="database schema tables must equal approved tables in order"
    ):
        validate_dictionary(malformed_schema, dictionary)


def test_rejects_missing_dictionary_column(schema: DatabaseSchema, dictionary: SchemaDictionary):
    runs = dictionary.tables["runs"]
    tables = dict(dictionary.tables)
    tables["runs"] = replace(
        runs,
        columns=MappingProxyType(
            {name: column for name, column in runs.columns.items() if name != "win"}
        ),
    )
    dictionary_without_win = SchemaDictionary(
        tables=MappingProxyType(tables), sha256=dictionary.sha256
    )

    with pytest.raises(ValueError, match="runs missing dictionary columns: win"):
        validate_dictionary(schema, dictionary_without_win)


def test_rejects_unknown_dictionary_column(schema: DatabaseSchema, dictionary: SchemaDictionary):
    runs = dictionary.tables["runs"]
    tables = dict(dictionary.tables)
    tables["runs"] = replace(
        runs,
        columns=MappingProxyType(
            {**runs.columns, "unknown": ColumnDescription("Unknown column.")}
        ),
    )
    dictionary_with_unknown_column = SchemaDictionary(
        tables=MappingProxyType(tables), sha256=dictionary.sha256
    )

    with pytest.raises(ValueError, match="runs unknown dictionary columns: unknown"):
        validate_dictionary(schema, dictionary_with_unknown_column)


def test_reports_missing_and_unknown_dictionary_columns_together(
    schema: DatabaseSchema, dictionary: SchemaDictionary
):
    runs = dictionary.tables["runs"]
    tables = dict(dictionary.tables)
    tables["runs"] = replace(
        runs,
        columns=MappingProxyType(
            {
                name: column
                for name, column in runs.columns.items()
                if name != "win"
            }
            | {"unknown": ColumnDescription("Unknown column.")}
        ),
    )
    invalid_dictionary = SchemaDictionary(
        tables=MappingProxyType(tables), sha256=dictionary.sha256
    )

    with pytest.raises(ValueError) as raised:
        validate_dictionary(schema, invalid_dictionary)

    assert str(raised.value) == (
        "runs missing dictionary columns: win; runs unknown dictionary columns: unknown"
    )


def test_loader_rejects_blank_description(project_root: Path, tmp_path: Path):
    dictionary_path = tmp_path / "blank-description.json"
    dictionary_path.write_text(
        '{"runs":{"description":" ","columns":{"run_id":{"description":"ID"}},"joins":[]}}'
    )

    from nlq.schema_dictionary import load_schema_dictionary

    with pytest.raises(ValueError, match="table description must be a non-blank string"):
        load_schema_dictionary(dictionary_path)


def test_rejects_join_to_unknown_table(schema: DatabaseSchema, dictionary: SchemaDictionary):
    run_cards = dictionary.tables["run_cards"]
    tables = dict(dictionary.tables)
    tables["run_cards"] = replace(
        run_cards,
        joins=(
            JoinDescription(
                table="unknown",
                on="run_cards.run_id = unknown.run_id",
                description="Invalid target.",
            ),
        ),
    )
    invalid_dictionary = SchemaDictionary(
        tables=MappingProxyType(tables), sha256=dictionary.sha256
    )

    with pytest.raises(ValueError, match="run_cards join targets unknown table: unknown"):
        validate_dictionary(schema, invalid_dictionary)


def test_rejects_join_with_unknown_column(schema: DatabaseSchema, dictionary: SchemaDictionary):
    run_cards = dictionary.tables["run_cards"]
    tables = dict(dictionary.tables)
    tables["run_cards"] = replace(
        run_cards,
        joins=(
            JoinDescription(
                table="runs",
                on="run_cards.unknown = runs.run_id",
                description="Invalid source column.",
            ),
        ),
    )
    invalid_dictionary = SchemaDictionary(
        tables=MappingProxyType(tables), sha256=dictionary.sha256
    )

    with pytest.raises(ValueError, match="join references unknown column: run_cards.unknown"):
        validate_dictionary(schema, invalid_dictionary)


@pytest.fixture
def schema(duckdb_analytical_database: Path) -> DatabaseSchema:
    return inspect_approved_schema(duckdb_analytical_database)


def test_render_is_stable(schema: DatabaseSchema, dictionary: SchemaDictionary, manifest):
    first = render_schema_context(schema, dictionary, manifest)
    second = render_schema_context(schema, dictionary, manifest)

    assert first.text == second.text
    assert first.sha256 == second.sha256
    assert first.sha256 == hashlib.sha256(first.text.encode()).hexdigest()
    assert first.text.endswith("\n")
    assert not first.text.endswith("\n\n")
    assert "Table: runs" in first.text
    assert "sync_state" not in first.text


def test_render_includes_verified_metadata(schema: DatabaseSchema, dictionary: SchemaDictionary, manifest):
    context = render_schema_context(schema, dictionary, manifest)

    assert context.dataset_id == "test-dataset"
    assert context.database_sha256 == "database-sha256"
    assert context.schema_git_commit == "abc123"
    assert context.dictionary_sha256 == dictionary.sha256
    assert context.approved_tables == APPROVED_TABLES


def test_render_uses_live_columns_and_dictionary_metadata(
    schema: DatabaseSchema, dictionary: SchemaDictionary, manifest
):
    context = render_schema_context(schema, dictionary, manifest)

    assert context.text.index("Table: runs") < context.text.index("Table: run_cards")
    assert context.text.index("run_id | VARCHAR | not null | primary key") < context.text.index(
        "character | VARCHAR | not null"
    )
    assert "Whether the run was won." in context.text
    assert "Values: 0 = loss; 1 = victory" in context.text
    assert "Range: 0 to 10" in context.text
    assert "Examples: IRONCLAD, SILENT, DEFECT, AUTOMATON-AUTOMATON" in context.text
    assert "Join: run_cards.run_id = runs.run_id" in context.text


def test_render_does_not_include_database_rows(
    duckdb_analytical_database: Path,
    schema: DatabaseSchema,
    dictionary: SchemaDictionary,
    manifest,
):
    with duckdb.connect(str(duckdb_analytical_database)) as connection:
        connection.execute(
            "INSERT INTO runs (run_id, character, win, was_abandoned, ascension) "
            "VALUES ('secret-run', 'SILENT', 1, 0, 0)"
        )

    context = render_schema_context(schema, dictionary, manifest)

    assert "secret-run" not in context.text


@pytest.mark.parametrize(
    "field",
    (
        "table_description",
        "column_description",
        "value_key",
        "value_description",
        "example",
        "join_table",
        "join_expression",
        "join_description",
    ),
)
@pytest.mark.parametrize("line_break", ("\n", "\r"))
def test_render_rejects_line_breaks_in_human_metadata(
    schema: DatabaseSchema,
    dictionary: SchemaDictionary,
    manifest,
    field: str,
    line_break: str,
):
    invalid_dictionary = _dictionary_with_metadata(
        dictionary, field, f"unsafe{line_break}metadata"
    )

    with pytest.raises(ValueError, match="metadata contains line break"):
        render_schema_context(schema, invalid_dictionary, manifest)


@pytest.mark.parametrize(
    ("field", "forbidden_name"),
    (
        ("table_description", "raw_runs"),
        ("value_description", "sync_state"),
        ("example", "sync_log"),
        ("join_expression", "sqlite_internal"),
    ),
)
def test_render_rejects_forbidden_names_in_human_metadata(
    schema: DatabaseSchema,
    dictionary: SchemaDictionary,
    manifest,
    field: str,
    forbidden_name: str,
):
    invalid_dictionary = _dictionary_with_metadata(dictionary, field, forbidden_name)

    with pytest.raises(ValueError, match=f"metadata contains forbidden name: {forbidden_name}"):
        render_schema_context(schema, invalid_dictionary, manifest)


def _dictionary_with_metadata(
    dictionary: SchemaDictionary, field: str, value: str
) -> SchemaDictionary:
    tables = dict(dictionary.tables)
    if field == "table_description":
        tables["runs"] = replace(tables["runs"], description=value)
    elif field == "column_description":
        tables["runs"] = _replace_column_description(tables["runs"], value)
    elif field in {"value_key", "value_description"}:
        tables["runs"] = _replace_column_values(tables["runs"], field, value)
    elif field == "example":
        tables["runs"] = _replace_column_examples(tables["runs"], value)
    else:
        tables["run_cards"] = _replace_join_metadata(tables["run_cards"], field, value)
    return SchemaDictionary(tables=MappingProxyType(tables), sha256=dictionary.sha256)


def _replace_column_description(table: TableDescription, value: str) -> TableDescription:
    columns = dict(table.columns)
    columns["win"] = replace(columns["win"], description=value)
    return replace(table, columns=MappingProxyType(columns))


def _replace_column_values(
    table: TableDescription, field: str, value: str
) -> TableDescription:
    columns = dict(table.columns)
    if field == "value_key":
        values = ((value, "loss"),)
    else:
        values = (("0", value),)
    columns["win"] = replace(columns["win"], values=values)
    return replace(table, columns=MappingProxyType(columns))


def _replace_column_examples(table: TableDescription, value: str) -> TableDescription:
    columns = dict(table.columns)
    columns["character"] = replace(columns["character"], examples=(value,))
    return replace(table, columns=MappingProxyType(columns))


def _replace_join_metadata(table: TableDescription, field: str, value: str) -> TableDescription:
    join = table.joins[0]
    if field == "join_table":
        join = replace(join, table=value)
    elif field == "join_expression":
        join = replace(join, on=value)
    else:
        join = replace(join, description=value)
    return replace(table, joins=(join,))
