import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias


ExampleValue: TypeAlias = str | int | float
Number: TypeAlias = int | float

_TABLE_FIELDS = frozenset({"description", "columns", "joins"})
_COLUMN_FIELDS = frozenset({"description", "values", "range", "examples"})
_RANGE_FIELDS = frozenset({"minimum", "maximum"})
_JOIN_FIELDS = frozenset({"table", "on", "description"})


@dataclass(frozen=True)
class ColumnDescription:
    description: str
    values: tuple[tuple[str, str], ...] = ()
    minimum: Number | None = None
    maximum: Number | None = None
    examples: tuple[ExampleValue, ...] = ()


@dataclass(frozen=True)
class JoinDescription:
    table: str
    on: str
    description: str


@dataclass(frozen=True)
class TableDescription:
    description: str
    columns: Mapping[str, ColumnDescription]
    joins: tuple[JoinDescription, ...]


@dataclass(frozen=True)
class SchemaDictionary:
    tables: Mapping[str, TableDescription]
    sha256: str


def load_schema_dictionary(path: str | Path) -> SchemaDictionary:
    file_path = Path(path)
    raw_bytes = file_path.read_bytes()
    document = json.loads(raw_bytes)

    if not isinstance(document, dict):
        raise ValueError("schema dictionary root must be a JSON object")

    tables = MappingProxyType(
        {
            table_name: _parse_table(table_name, table_value)
            for table_name, table_value in document.items()
        }
    )
    return SchemaDictionary(
        tables=tables,
        sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _parse_table(table_name: object, value: object) -> TableDescription:
    name = _non_blank_string(table_name, "table name")
    table = _object(value, f"table {name}")
    _require_exact_fields(table, _TABLE_FIELDS, "table")
    _require_fields(table, _TABLE_FIELDS, "table")

    columns_value = table.get("columns")
    columns = _object(columns_value, "table columns")
    if not columns:
        raise ValueError("table columns must not be empty")

    joins_value = table.get("joins")
    if not isinstance(joins_value, list):
        raise ValueError("joins must be a list")

    return TableDescription(
        description=_non_blank_string(table.get("description"), "table description"),
        columns=MappingProxyType(
            {
                _non_blank_string(column_name, "column name"): _parse_column(
                    column_value
                )
                for column_name, column_value in columns.items()
            }
        ),
        joins=_parse_joins(joins_value),
    )


def _parse_column(value: object) -> ColumnDescription:
    column = _object(value, "column")
    _require_exact_fields(column, _COLUMN_FIELDS, "column")
    _require_fields(column, {"description"}, "column")

    minimum, maximum = _parse_range(column.get("range")) if "range" in column else (None, None)
    return ColumnDescription(
        description=_non_blank_string(
            column.get("description"), "column description"
        ),
        values=_parse_values(column.get("values")) if "values" in column else (),
        minimum=minimum,
        maximum=maximum,
        examples=_parse_examples(column.get("examples")) if "examples" in column else (),
    )


def _parse_values(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, dict) or not value:
        raise ValueError("column values must be an object of non-blank strings")

    values = tuple(value.items())
    if not all(_is_non_blank_string(key) and _is_non_blank_string(description) for key, description in values):
        raise ValueError("column values must be an object of non-blank strings")
    return values


def _parse_range(value: object) -> tuple[Number, Number]:
    value_range = _object(value, "column range")
    _require_exact_fields(value_range, _RANGE_FIELDS, "column range")
    _require_fields(value_range, _RANGE_FIELDS, "column range")

    minimum = _number(value_range.get("minimum"), "column range minimum")
    maximum = _number(value_range.get("maximum"), "column range maximum")
    if minimum > maximum:
        raise ValueError("column range minimum must not exceed maximum")
    return minimum, maximum


def _parse_examples(value: object) -> tuple[ExampleValue, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("column examples must be a non-empty list")
    if not all(_is_example_value(example) for example in value):
        raise ValueError("column examples must contain non-blank strings or finite numbers")
    return tuple(value)


def _parse_joins(value: list[object]) -> tuple[JoinDescription, ...]:
    joins: list[JoinDescription] = []
    seen: set[tuple[str, str]] = set()
    for join_value in value:
        join = _object(join_value, "joins entry")
        _require_exact_fields(join, _JOIN_FIELDS, "joins entry")
        _require_fields(join, _JOIN_FIELDS, "joins entry")
        parsed_join = JoinDescription(
            table=_non_blank_string(join.get("table"), "joins table"),
            on=_non_blank_string(join.get("on"), "joins on"),
            description=_non_blank_string(join.get("description"), "joins description"),
        )
        identity = (parsed_join.table, parsed_join.on)
        if identity in seen:
            raise ValueError("duplicate join entry")
        seen.add(identity)
        joins.append(parsed_join)
    return tuple(joins)


def _object(value: object, name: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _require_exact_fields(
    value: dict[str, object], allowed_fields: frozenset[str], name: str
) -> None:
    unknown_fields = value.keys() - allowed_fields
    if unknown_fields:
        fields = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown {name} fields: {fields}")
def _require_fields(
    value: dict[str, object], required_fields: frozenset[str] | set[str], name: str
) -> None:
    missing_fields = required_fields - value.keys()
    if missing_fields:
        fields = ", ".join(sorted(missing_fields))
        raise ValueError(f"{name} fields are required: {fields}")


def _non_blank_string(value: object, name: str) -> str:
    if not _is_non_blank_string(value):
        raise ValueError(f"{name} must be a non-blank string")
    return value


def _is_non_blank_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _number(value: object, name: str) -> Number:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    return value


def _is_example_value(value: object) -> bool:
    if _is_non_blank_string(value):
        return True
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value)
