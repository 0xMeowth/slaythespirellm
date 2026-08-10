import hashlib
import json
from pathlib import Path

import pytest

from nlq.schema_dictionary import (
    ColumnDescription,
    JoinDescription,
    SchemaDictionary,
    TableDescription,
    load_schema_dictionary,
)


_UNSET = object()


@pytest.fixture
def dictionary_path() -> Path:
    return Path(__file__).parents[2] / "nlq" / "schema_dictionary.json"


def write_dictionary(
    tmp_path: Path,
    *,
    root: object = _UNSET,
    table: object = _UNSET,
    column: object = _UNSET,
    joins: object = _UNSET,
) -> Path:
    document = (
        root
        if root is not _UNSET
        else {
            "example": table
            if table is not _UNSET
            else {
                "description": "Example table.",
                "columns": {
                    "value": column
                    if column is not _UNSET
                    else {"description": "Example value."}
                },
                "joins": joins if joins is not _UNSET else [],
            }
        }
    )
    path = tmp_path / "dictionary.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_loads_typed_dictionary(dictionary_path: Path):
    dictionary = load_schema_dictionary(dictionary_path)

    assert dictionary.tables["runs"].columns["win"].values == (
        ("0", "loss"),
        ("1", "victory"),
    )
    assert isinstance(dictionary.tables["runs"], TableDescription)
    assert isinstance(dictionary.tables["runs"].columns["win"], ColumnDescription)
    assert isinstance(dictionary.tables["run_cards"].joins[0], JoinDescription)
    assert dictionary.sha256 == hashlib.sha256(dictionary_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("root", [[], "runs", 1, None])
def test_rejects_non_object_root(tmp_path: Path, root: object):
    path = write_dictionary(tmp_path, root=root)

    with pytest.raises(ValueError, match="root must be an object"):
        load_schema_dictionary(path)


def test_rejects_unknown_table_metadata(tmp_path: Path):
    path = write_dictionary(
        tmp_path,
        table={
            "description": "Example table.",
            "columns": {"value": {"description": "Example value."}},
            "joins": [],
            "unsupported": True,
        },
    )

    with pytest.raises(ValueError, match="unknown table fields"):
        load_schema_dictionary(path)


def test_rejects_unknown_column_metadata(tmp_path: Path):
    path = write_dictionary(
        tmp_path,
        column={"description": "Meaning", "unsupported": True},
    )

    with pytest.raises(ValueError, match="unknown column fields"):
        load_schema_dictionary(path)


@pytest.mark.parametrize("description", ["", "  ", None, 1])
def test_rejects_blank_or_invalid_descriptions(tmp_path: Path, description: object):
    path = write_dictionary(
        tmp_path,
        column={"description": description},
    )

    with pytest.raises(ValueError, match="column description must be a non-blank string"):
        load_schema_dictionary(path)


@pytest.mark.parametrize(
    "values",
    [
        [],
        {"0": 0},
        {"": "loss"},
        {"0": " "},
    ],
)
def test_rejects_invalid_value_maps(tmp_path: Path, values: object):
    path = write_dictionary(
        tmp_path,
        column={"description": "Meaning.", "values": values},
    )

    with pytest.raises(ValueError, match="column values must be an object of non-blank strings"):
        load_schema_dictionary(path)


@pytest.mark.parametrize(
    "value_range",
    [
        [],
        {"minimum": 0},
        {"minimum": 0, "maximum": 1, "extra": 2},
        {"minimum": "0", "maximum": 1},
        {"minimum": True, "maximum": 1},
        {"minimum": 2, "maximum": 1},
    ],
)
def test_rejects_invalid_ranges(tmp_path: Path, value_range: object):
    path = write_dictionary(
        tmp_path,
        column={"description": "Meaning.", "range": value_range},
    )

    with pytest.raises(ValueError, match="column range"):
        load_schema_dictionary(path)


@pytest.mark.parametrize("examples", ["one", [], [True], [{}], [" "]])
def test_rejects_invalid_examples(tmp_path: Path, examples: object):
    path = write_dictionary(
        tmp_path,
        column={"description": "Meaning.", "examples": examples},
    )

    with pytest.raises(ValueError, match="column examples"):
        load_schema_dictionary(path)


@pytest.mark.parametrize(
    "joins",
    [
        {},
        [{"table": "other", "on": "example.id = other.id"}],
        [
            {
                "table": "other",
                "on": "example.id = other.id",
                "description": "Join.",
                "extra": True,
            }
        ],
        [{"table": " ", "on": "example.id = other.id", "description": "Join."}],
    ],
)
def test_rejects_malformed_joins(tmp_path: Path, joins: object):
    path = write_dictionary(tmp_path, joins=joins)

    with pytest.raises(ValueError, match="joins"):
        load_schema_dictionary(path)


def test_rejects_duplicate_join_entries(tmp_path: Path):
    join = {
        "table": "other",
        "on": "example.id = other.id",
        "description": "Join.",
    }
    path = write_dictionary(tmp_path, joins=[join, join])

    with pytest.raises(ValueError, match="duplicate join"):
        load_schema_dictionary(path)


def test_is_immutable(dictionary_path: Path):
    dictionary = load_schema_dictionary(dictionary_path)

    with pytest.raises(AttributeError):
        dictionary.sha256 = "other"


def test_rejects_schema_table_mapping_mutation(dictionary_path: Path):
    dictionary = load_schema_dictionary(dictionary_path)

    with pytest.raises(TypeError):
        dictionary.tables["other"] = dictionary.tables["runs"]


def test_rejects_table_column_mapping_mutation(dictionary_path: Path):
    dictionary = load_schema_dictionary(dictionary_path)

    with pytest.raises(TypeError):
        dictionary.tables["runs"].columns["other"] = dictionary.tables["runs"].columns[
            "win"
        ]
