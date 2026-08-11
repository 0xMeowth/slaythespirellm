import hashlib
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Lock

from eval.manifest import load_manifest, verify_manifest
from eval.models import DatasetManifest
from nlq.schema_dictionary import SchemaDictionary, load_schema_dictionary


APPROVED_TABLES = (
    "runs",
    "run_cards",
    "run_relics",
    "run_card_choices",
    "cards",
    "relics",
)
RENDERER_VERSION = "1"
_JOIN_EXPRESSION = re.compile(
    r"(?P<left_table>[A-Za-z_][A-Za-z0-9_]*)\.(?P<left_column>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*=\s*"
    r"(?P<right_table>[A-Za-z_][A-Za-z0-9_]*)\.(?P<right_column>[A-Za-z_][A-Za-z0-9_]*)"
)
_FORBIDDEN_METADATA_NAME = re.compile(
    r"(?<![A-Za-z0-9_])(raw_runs|sync_state|sync_log|sqlite_[A-Za-z0-9_]*)(?![A-Za-z0-9_])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ColumnSchema:
    name: str
    sqlite_type: str
    nullable: bool
    primary_key: bool


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[ColumnSchema, ...]


@dataclass(frozen=True)
class DatabaseSchema:
    tables: tuple[TableSchema, ...]


@dataclass(frozen=True)
class SchemaContext:
    text: str
    sha256: str
    dataset_id: str
    database_sha256: str
    schema_git_commit: str
    dictionary_sha256: str
    approved_tables: tuple[str, ...]


@dataclass(frozen=True)
class SchemaContextCacheKey:
    dataset_id: str
    database_sha256: str
    schema_git_commit: str
    dictionary_sha256: str
    renderer_version: str


class SchemaContextCache:
    def __init__(self) -> None:
        self._contexts: dict[SchemaContextCacheKey, SchemaContext] = {}
        self._lock = Lock()

    def get_or_build(
        self,
        *,
        dataset_id: str,
        database_sha256: str,
        schema_git_commit: str,
        dictionary_sha256: str,
        renderer_version: str,
        builder: Callable[[], SchemaContext],
    ) -> SchemaContext:
        key = SchemaContextCacheKey(
            dataset_id=dataset_id,
            database_sha256=database_sha256,
            schema_git_commit=schema_git_commit,
            dictionary_sha256=dictionary_sha256,
            renderer_version=renderer_version,
        )
        with self._lock:
            context = self._contexts.get(key)
            if context is None:
                context = builder()
                self._contexts[key] = context
            return context


_schema_context_cache = SchemaContextCache()


def inspect_approved_schema(database: Path) -> DatabaseSchema:
    uri = f"file:{database.resolve()}?mode=ro"
    tables: list[TableSchema] = []
    with sqlite3.connect(uri, uri=True) as connection:
        for table_name in APPROVED_TABLES:
            rows = connection.execute(f'PRAGMA table_info("{table_name}")').fetchall()
            if not rows:
                raise ValueError(f"missing approved table: {table_name}")
            tables.append(
                TableSchema(
                    name=table_name,
                    columns=tuple(
                        ColumnSchema(
                            name=str(row[1]),
                            sqlite_type=str(row[2]),
                            nullable=not bool(row[3]),
                            primary_key=bool(row[5]),
                        )
                        for row in rows
                    ),
                )
            )
    return DatabaseSchema(tables=tuple(tables))


def validate_dictionary(schema: DatabaseSchema, dictionary: SchemaDictionary) -> None:
    _validate_schema_table_sequence(schema)
    schema_tables = {table.name: table for table in schema.tables}
    _validate_exact_names(
        actual=set(dictionary.tables), expected=set(schema_tables), subject="dictionary tables"
    )

    for table_name in APPROVED_TABLES:
        table_schema = schema_tables[table_name]
        table_dictionary = dictionary.tables[table_name]
        schema_columns = {column.name for column in table_schema.columns}
        _validate_exact_names(
            actual=set(table_dictionary.columns),
            expected=schema_columns,
            subject="dictionary columns",
            table_name=table_name,
        )
        _validate_joins(table_name, table_dictionary.joins, schema_tables)


def render_schema_context(
    schema: DatabaseSchema, dictionary: SchemaDictionary, manifest: DatasetManifest
) -> SchemaContext:
    _validate_schema_table_sequence(schema)
    _validate_rendered_metadata(schema, dictionary)
    validate_dictionary(schema, dictionary)
    schema_tables = {table.name: table for table in schema.tables}
    lines = [
        f"Dataset: {manifest.dataset_id}",
        f"Database SHA-256: {manifest.database_sha256}",
        f"Schema git commit: {manifest.schema_git_commit}",
        f"Dictionary SHA-256: {dictionary.sha256}",
        f"Renderer version: {RENDERER_VERSION}",
        f"Approved tables: {', '.join(APPROVED_TABLES)}",
    ]

    for table_name in APPROVED_TABLES:
        table_schema = schema_tables[table_name]
        table_dictionary = dictionary.tables[table_name]
        lines.extend(("", f"Table: {table_name}", f"Description: {table_dictionary.description}", "Columns:"))
        for column_schema in table_schema.columns:
            column_dictionary = table_dictionary.columns[column_schema.name]
            lines.append(f"- {_render_column_schema(column_schema)}")
            lines.append(f"  Description: {column_dictionary.description}")
            if column_dictionary.values:
                values = "; ".join(
                    f"{value} = {description}"
                    for value, description in sorted(column_dictionary.values)
                )
                lines.append(f"  Values: {values}")
            if column_dictionary.minimum is not None:
                lines.append(
                    f"  Range: {column_dictionary.minimum} to {column_dictionary.maximum}"
                )
            if column_dictionary.examples:
                examples = ", ".join(str(example) for example in column_dictionary.examples)
                lines.append(f"  Examples: {examples}")
        if table_dictionary.joins:
            lines.append("Joins:")
            for join in sorted(
                table_dictionary.joins,
                key=lambda item: (item.table, item.on, item.description),
            ):
                lines.append(f"- Join: {join.on} — {join.description}")

    text = "\n".join(lines) + "\n"
    return SchemaContext(
        text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        dataset_id=manifest.dataset_id,
        database_sha256=manifest.database_sha256,
        schema_git_commit=manifest.schema_git_commit,
        dictionary_sha256=dictionary.sha256,
        approved_tables=APPROVED_TABLES,
    )


def build_verified_schema_context(
    manifest_path: Path,
    dictionary_path: Path,
    *,
    project_root: Path,
    cache: SchemaContextCache | None = None,
) -> SchemaContext:
    manifest = load_manifest(manifest_path)
    database = verify_manifest(manifest, project_root)
    dictionary = load_schema_dictionary(dictionary_path)
    context_cache = cache if cache is not None else _schema_context_cache

    def build() -> SchemaContext:
        schema = inspect_approved_schema(database)
        validate_dictionary(schema, dictionary)
        return render_schema_context(schema, dictionary, manifest)

    return context_cache.get_or_build(
        dataset_id=manifest.dataset_id,
        database_sha256=manifest.database_sha256,
        schema_git_commit=manifest.schema_git_commit,
        dictionary_sha256=dictionary.sha256,
        renderer_version=RENDERER_VERSION,
        builder=build,
    )


def _validate_exact_names(
    *, actual: set[str], expected: set[str], subject: str, table_name: str | None = None
) -> None:
    missing = expected - actual
    unknown = actual - expected
    prefix = f"{table_name} " if table_name is not None else ""
    errors = []
    if missing:
        errors.append(f"{prefix}missing {subject}: {', '.join(sorted(missing))}")
    if unknown:
        errors.append(f"{prefix}unknown {subject}: {', '.join(sorted(unknown))}")
    if errors:
        raise ValueError("; ".join(errors))


def _validate_schema_table_sequence(schema: DatabaseSchema) -> None:
    table_names = tuple(table.name for table in schema.tables)
    if table_names != APPROVED_TABLES:
        raise ValueError(
            "database schema tables must equal approved tables in order: "
            f"expected {', '.join(APPROVED_TABLES)}; got {', '.join(table_names)}"
        )


def _validate_rendered_metadata(
    schema: DatabaseSchema, dictionary: SchemaDictionary
) -> None:
    for table_schema in schema.tables:
        table_dictionary = dictionary.tables.get(table_schema.name)
        if table_dictionary is None:
            continue
        _validate_metadata_text(table_dictionary.description)
        for column_schema in table_schema.columns:
            column_dictionary = table_dictionary.columns.get(column_schema.name)
            if column_dictionary is None:
                continue
            _validate_metadata_text(column_dictionary.description)
            for value, description in sorted(column_dictionary.values):
                _validate_metadata_text(value)
                _validate_metadata_text(description)
            for example in column_dictionary.examples:
                if isinstance(example, str):
                    _validate_metadata_text(example)
        for join in sorted(
            table_dictionary.joins,
            key=lambda item: (item.table, item.on, item.description),
        ):
            _validate_metadata_text(join.table)
            _validate_metadata_text(join.on)
            _validate_metadata_text(join.description)


def _validate_metadata_text(value: str) -> None:
    if "\n" in value or "\r" in value:
        raise ValueError("metadata contains line break")
    forbidden_name = _FORBIDDEN_METADATA_NAME.search(value)
    if forbidden_name is not None:
        raise ValueError(f"metadata contains forbidden name: {forbidden_name.group()}")


def _validate_joins(table_name: str, joins, schema_tables: dict[str, TableSchema]) -> None:
    columns_by_table = {
        name: {column.name for column in table.columns}
        for name, table in schema_tables.items()
    }
    for join in joins:
        if join.table not in schema_tables:
            raise ValueError(f"{table_name} join targets unknown table: {join.table}")
        expression = _JOIN_EXPRESSION.fullmatch(join.on)
        if expression is None:
            raise ValueError(f"invalid join expression: {join.on}")
        left_table = expression["left_table"]
        right_table = expression["right_table"]
        if left_table != table_name:
            raise ValueError(f"{table_name} join must originate from {table_name}")
        if right_table != join.table:
            raise ValueError(f"{table_name} join target does not match expression: {join.table}")
        for referenced_table, referenced_column in (
            (left_table, expression["left_column"]),
            (right_table, expression["right_column"]),
        ):
            if referenced_column not in columns_by_table[referenced_table]:
                raise ValueError(
                    "join references unknown column: "
                    f"{referenced_table}.{referenced_column}"
                )


def _render_column_schema(column: ColumnSchema) -> str:
    attributes = [
        column.name,
        column.sqlite_type,
        "nullable" if column.nullable else "not null",
    ]
    if column.primary_key:
        attributes.append("primary key")
    return " | ".join(attributes)
