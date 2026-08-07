import hashlib
import json
import sqlite3

import pytest

from eval.manifest import (
    create_manifest,
    inspect_runs,
    load_manifest,
    sha256_file,
    verify_manifest,
    write_manifest,
)


def create_runs_database(path, rows):
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE runs (submitted_at TEXT)")
        connection.executemany(
            "INSERT INTO runs (submitted_at) VALUES (?)",
            [(submitted_at,) for submitted_at in rows],
        )


def sample_manifest(database):
    return create_manifest(
        dataset_id="test-snapshot",
        database=database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        run_count=2,
        earliest_run_date="2026-06-01",
        latest_run_date="2026-07-29",
        created_at="2026-08-07T00:00:00+00:00",
    )


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "snapshot.db"
    path.write_bytes(b"snapshot")

    assert sha256_file(path) == hashlib.sha256(b"snapshot").hexdigest()


def test_manifest_round_trips_as_json(tmp_path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"snapshot")
    output = tmp_path / "manifest.json"

    write_manifest(sample_manifest(database), output)

    assert load_manifest(output) == sample_manifest(database)
    assert output.read_text().endswith("\n")
    assert json.loads(output.read_text())["database"] == "snapshot.db"


def test_verify_manifest_returns_database_path(tmp_path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"snapshot")

    verified = verify_manifest(sample_manifest(database), tmp_path)

    assert verified == database.resolve()


def test_verify_manifest_rejects_changed_database(tmp_path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"before")
    manifest = sample_manifest(database)
    database.write_bytes(b"after")

    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_manifest(manifest, tmp_path)


def test_verify_manifest_rejects_missing_database(tmp_path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"snapshot")
    manifest = sample_manifest(database)
    database.unlink()

    with pytest.raises(FileNotFoundError, match="snapshot.db"):
        verify_manifest(manifest, tmp_path)


def test_create_manifest_rejects_absolute_database_path(tmp_path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"snapshot")

    with pytest.raises(ValueError, match="relative"):
        create_manifest(
            dataset_id="test-snapshot",
            database=database,
            manifest_database_path=str(database.resolve()),
            schema_git_commit="abc123",
            run_count=1,
            earliest_run_date="2026-06-01",
            latest_run_date="2026-07-29",
            created_at="2026-08-07T00:00:00+00:00",
        )


def test_inspect_runs_returns_count_and_coverage(tmp_path):
    database = tmp_path / "runs.db"
    create_runs_database(database, ["2026-07-29", "2026-06-01"])

    assert inspect_runs(database) == (2, "2026-06-01", "2026-07-29")


def test_inspect_runs_rejects_empty_runs_table(tmp_path):
    database = tmp_path / "runs.db"
    create_runs_database(database, [])

    with pytest.raises(ValueError, match="no dated rows"):
        inspect_runs(database)


def test_load_manifest_rejects_unknown_fields(tmp_path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"snapshot")
    output = tmp_path / "manifest.json"
    write_manifest(sample_manifest(database), output)
    raw = json.loads(output.read_text())
    raw["notes"] = "unexpected"
    output.write_text(json.dumps(raw))

    with pytest.raises(ValueError, match="unknown manifest fields"):
        load_manifest(output)
