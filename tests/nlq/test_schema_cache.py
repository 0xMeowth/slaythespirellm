from pathlib import Path

import pytest

from eval.manifest import create_manifest, write_manifest
from nlq.schema_context import (
    APPROVED_TABLES,
    RENDERER_VERSION,
    SchemaContext,
    SchemaContextCache,
    build_verified_schema_context,
)


@pytest.fixture
def cache_inputs():
    return {
        "dataset_id": "dataset",
        "database_sha256": "database-sha",
        "schema_git_commit": "commit-a",
        "dictionary_sha256": "dictionary-sha",
        "renderer_version": RENDERER_VERSION,
    }


def test_cache_reuses_identical_context(cache_inputs):
    calls = 0
    cache = SchemaContextCache()

    def counting_builder():
        nonlocal calls
        calls += 1
        return _context()

    first = cache.get_or_build(**cache_inputs, builder=counting_builder)
    second = cache.get_or_build(**cache_inputs, builder=counting_builder)

    assert first is second
    assert calls == 1


@pytest.mark.parametrize(
    ("changed_field", "value"),
    [
        ("dataset_id", "dataset-2"),
        ("database_sha256", "database-sha-2"),
        ("schema_git_commit", "commit-b"),
        ("dictionary_sha256", "dictionary-sha-2"),
        ("renderer_version", "3"),
    ],
)
def test_cache_misses_when_key_metadata_changes(cache_inputs, changed_field: str, value: str):
    calls = 0
    cache = SchemaContextCache()

    def counting_builder():
        nonlocal calls
        calls += 1
        return _context()

    cache.get_or_build(**cache_inputs, builder=counting_builder)
    changed_inputs = {**cache_inputs, changed_field: value}
    cache.get_or_build(**changed_inputs, builder=counting_builder)

    assert calls == 2


def test_build_verifies_manifest_and_caches_full_build(
    duckdb_manifest_database: Path, project_root: Path, tmp_path: Path, monkeypatch
):
    database = tmp_path / "snapshot.db"
    database.write_bytes(duckdb_manifest_database.read_bytes())
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    manifest = create_manifest(
        dataset_id="test-dataset",
        database=database,
        source_database=source_database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        created_at="2026-08-10T00:00:00+00:00",
    )
    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest, manifest_path)
    cache = SchemaContextCache()
    calls = 0

    from nlq import schema_context

    real_inspect = schema_context.inspect_approved_schema

    def counting_inspect(path: Path):
        nonlocal calls
        calls += 1
        return real_inspect(path)

    monkeypatch.setattr(schema_context, "inspect_approved_schema", counting_inspect)
    dictionary_path = project_root / "nlq" / "schema_dictionary.json"

    first = build_verified_schema_context(
        manifest_path, dictionary_path, project_root=tmp_path, cache=cache
    )
    second = build_verified_schema_context(
        manifest_path, dictionary_path, project_root=tmp_path, cache=cache
    )

    assert first is second
    assert calls == 1


def test_build_does_not_share_contexts_between_datasets(
    duckdb_manifest_database: Path, project_root: Path, tmp_path: Path, monkeypatch
):
    database = tmp_path / "snapshot.db"
    database.write_bytes(duckdb_manifest_database.read_bytes())
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    first_manifest = create_manifest(
        dataset_id="first-dataset",
        database=database,
        source_database=source_database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        created_at="2026-08-10T00:00:00+00:00",
    )
    second_manifest = create_manifest(
        dataset_id="second-dataset",
        database=database,
        source_database=source_database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        created_at="2026-08-10T00:00:00+00:00",
    )
    first_manifest_path = tmp_path / "first-manifest.json"
    second_manifest_path = tmp_path / "second-manifest.json"
    write_manifest(first_manifest, first_manifest_path)
    write_manifest(second_manifest, second_manifest_path)
    cache = SchemaContextCache()
    calls = 0

    from nlq import schema_context

    real_inspect = schema_context.inspect_approved_schema

    def counting_inspect(path: Path):
        nonlocal calls
        calls += 1
        return real_inspect(path)

    monkeypatch.setattr(schema_context, "inspect_approved_schema", counting_inspect)
    dictionary_path = project_root / "nlq" / "schema_dictionary.json"

    first = build_verified_schema_context(
        first_manifest_path, dictionary_path, project_root=tmp_path, cache=cache
    )
    second = build_verified_schema_context(
        second_manifest_path, dictionary_path, project_root=tmp_path, cache=cache
    )

    assert first is not second
    assert first.dataset_id == "first-dataset"
    assert second.dataset_id == "second-dataset"
    assert first.database_sha256 == second.database_sha256
    assert first.schema_git_commit == second.schema_git_commit
    assert first.dictionary_sha256 == second.dictionary_sha256
    assert calls == 2


def _context() -> SchemaContext:
    return SchemaContext(
        text="schema\n",
        sha256="context-sha",
        dataset_id="dataset",
        database_sha256="database-sha",
        schema_git_commit="commit-a",
        dictionary_sha256="dictionary-sha",
        approved_tables=APPROVED_TABLES,
    )
