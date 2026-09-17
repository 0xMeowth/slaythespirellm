import hashlib
import json
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest

from eval.manifest import (
    create_manifest,
    inspect_runs,
    load_manifest,
    sha256_file,
    verify_manifest,
    write_manifest,
)
from eval.snapshot import ANALYTICAL_TABLES


def create_analytical_database(path: Path, project_root: Path, rows=()):
    with duckdb.connect(str(path)) as connection:
        connection.execute((project_root / "duckdb_analytics_schema.sql").read_text())
        connection.executemany(
            "INSERT INTO runs "
            "(run_id, character, win, was_abandoned, ascension, submitted_at) "
            "VALUES (?, 'SILENT', 0, 0, 0, ?)",
            [(f"run-{index}", submitted_at) for index, submitted_at in enumerate(rows)],
        )


def sample_manifest(database, source_database):
    return create_manifest(
        dataset_id="test-snapshot",
        database=database,
        source_database=source_database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        created_at="2026-08-07T00:00:00+00:00",
    )


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "snapshot.db"
    path.write_bytes(b"snapshot")

    assert sha256_file(path) == hashlib.sha256(b"snapshot").hexdigest()


def test_duckdb_manifest_round_trips_as_json(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(
        database,
        project_root,
        ["2026-06-01", "2026-07-29"],
    )
    output = tmp_path / "manifest.json"
    manifest = sample_manifest(database, source_database)

    write_manifest(manifest, output)

    loaded = load_manifest(output)
    assert loaded == manifest
    assert loaded.engine == "duckdb"
    assert loaded.engine_version == duckdb.__version__
    assert loaded.source_database_sha256 == sha256_file(source_database)
    assert output.read_text().endswith("\n")
    assert json.loads(output.read_text())["database"] == "snapshot.db"


def test_verify_manifest_returns_database_path(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])

    verified = verify_manifest(sample_manifest(database, source_database), tmp_path)

    assert verified == database.resolve()


def test_verify_manifest_rejects_changed_database(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])
    manifest = sample_manifest(database, source_database)
    with duckdb.connect(str(database)) as connection:
        connection.execute("INSERT INTO cards (card_id, name) VALUES ('X', 'X')")

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_manifest(manifest, tmp_path)


def test_verify_manifest_rejects_missing_database(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])
    manifest = sample_manifest(database, source_database)
    database.unlink()

    with pytest.raises(FileNotFoundError, match="snapshot.db"):
        verify_manifest(manifest, tmp_path)


def test_create_manifest_rejects_absolute_database_path(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])

    with pytest.raises(ValueError, match="relative"):
        create_manifest(
            dataset_id="test-snapshot",
            database=database,
            source_database=source_database,
            manifest_database_path=str(database.resolve()),
            schema_git_commit="abc123",
            created_at="2026-08-07T00:00:00+00:00",
        )


def test_inspect_runs_returns_count_and_coverage(tmp_path):
    database = tmp_path / "runs.db"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE runs (submitted_at VARCHAR)")
        connection.executemany(
            "INSERT INTO runs VALUES (?)",
            [("2026-07-29",), ("2026-06-01",)],
        )

    assert inspect_runs(database) == (2, "2026-06-01", "2026-07-29")


def test_inspect_runs_rejects_empty_runs_table(tmp_path):
    database = tmp_path / "runs.db"
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE runs (submitted_at VARCHAR)")

    with pytest.raises(ValueError, match="no dated rows"):
        inspect_runs(database)


def test_load_manifest_rejects_unknown_fields(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])
    output = tmp_path / "manifest.json"
    write_manifest(sample_manifest(database, source_database), output)
    raw = json.loads(output.read_text())
    raw["notes"] = "unexpected"
    output.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="unknown manifest fields"):
        load_manifest(output)


def test_verify_manifest_rejects_wrong_engine(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])
    manifest = replace(
        sample_manifest(database, source_database),
        engine="sqlite",
    )

    with pytest.raises(ValueError, match="manifest engine must be duckdb"):
        verify_manifest(manifest, tmp_path)


def test_verify_manifest_rejects_missing_approved_table(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])
    with duckdb.connect(str(database)) as connection:
        connection.execute("DROP TABLE relics")
    manifest = sample_manifest(database, source_database)

    with pytest.raises(ValueError, match="missing analytical tables: relics"):
        verify_manifest(manifest, tmp_path)


def test_verify_manifest_rejects_run_count_mismatch(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])
    manifest = replace(
        sample_manifest(database, source_database),
        run_count=2,
    )

    with pytest.raises(ValueError, match="run count mismatch"):
        verify_manifest(manifest, tmp_path)


def test_verify_manifest_requires_exact_approved_tables(tmp_path, project_root):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_analytical_database(database, project_root, ["2026-06-01"])
    manifest = sample_manifest(database, source_database)

    with duckdb.connect(str(database), read_only=True) as connection:
        table_names = {
            row[0] for row in connection.execute("SHOW TABLES").fetchall()
        }

    assert table_names == set(ANALYTICAL_TABLES)
