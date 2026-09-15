from pathlib import Path

import duckdb
import pytest

from eval import snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "duckdb_analytics_schema.sql"


def create_source_database(path):
    with duckdb.connect(str(path)) as connection:
        connection.execute(SCHEMA_PATH.read_text())
        connection.execute("CREATE TABLE raw_runs (run_id VARCHAR PRIMARY KEY)")
        connection.execute("CREATE TABLE sync_state (key VARCHAR PRIMARY KEY)")
        connection.execute("CREATE TABLE sync_log (id BIGINT PRIMARY KEY)")
        connection.execute(
            "INSERT INTO cards (card_id, name) VALUES ('GOLD_AXE', 'Gold Axe')"
        )
        connection.execute(
            "INSERT INTO relics (relic_id, name) "
            "VALUES ('FISHING_ROD', 'Fishing Rod')"
        )
        connection.execute(
            "INSERT INTO runs "
            "(run_id, character, win, was_abandoned, ascension) "
            "VALUES ('run-1', 'SILENT', 1, 0, 10)"
        )
        connection.execute(
            "INSERT INTO run_cards (run_id, card_id, upgrade_level) "
            "VALUES ('run-1', 'GOLD_AXE', 0)"
        )
        connection.execute(
            "INSERT INTO run_relics (run_id, relic_id) "
            "VALUES ('run-1', 'FISHING_ROD')"
        )
        connection.execute(
            "INSERT INTO run_card_choices "
            "(run_id, act, floor_index, card_id, was_picked) "
            "VALUES ('run-1', 1, 1, 'GOLD_AXE', 1)"
        )


def attach_test_source(connection, source):
    source_literal = str(source.resolve()).replace("'", "''")
    connection.execute(
        f"ATTACH '{source_literal}' AS source_sqlite (READ_ONLY)"
    )


def test_schema_creates_only_approved_tables(tmp_path):
    database = tmp_path / "snapshot.duckdb"

    snapshot.apply_duckdb_schema(database, SCHEMA_PATH)

    with duckdb.connect(str(database), read_only=True) as connection:
        names = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}

    assert names == set(snapshot.ANALYTICAL_TABLES)


def test_duckdb_schema_preserves_primary_keys(tmp_path):
    database = tmp_path / "snapshot.duckdb"
    snapshot.apply_duckdb_schema(database, SCHEMA_PATH)

    with duckdb.connect(str(database), read_only=True) as connection:
        rows = connection.execute(
            "SELECT table_name, constraint_column_names "
            "FROM duckdb_constraints() "
            "WHERE constraint_type = 'PRIMARY KEY'"
        ).fetchall()

    assert {table: tuple(columns) for table, columns in rows} == {
        "cards": ("card_id",),
        "relics": ("relic_id",),
        "runs": ("run_id",),
    }


def test_creates_native_snapshot_with_only_analytical_tables(tmp_path, monkeypatch):
    source = tmp_path / "source.duckdb"
    output = tmp_path / "snapshot.duckdb"
    link = tmp_path / "current.duckdb"
    create_source_database(source)
    monkeypatch.setattr(snapshot, "_attach_sqlite_source", attach_test_source)

    counts = snapshot.create_snapshot(source, output, SCHEMA_PATH, link)

    with duckdb.connect(str(output), read_only=True) as connection:
        tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
        runs = connection.execute(
            "SELECT run_id, character FROM runs"
        ).fetchall()

    assert tables == set(snapshot.ANALYTICAL_TABLES)
    assert "raw_runs" not in tables
    assert "sync_state" not in tables
    assert "sync_log" not in tables
    assert runs == [("run-1", "SILENT")]
    assert counts == {table: 1 for table in snapshot.ANALYTICAL_TABLES}
    assert link.is_symlink()
    assert link.resolve() == output.resolve()


def test_rejects_existing_output(tmp_path):
    source = tmp_path / "source.duckdb"
    output = tmp_path / "snapshot.duckdb"
    create_source_database(source)
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="snapshot already exists"):
        snapshot.create_snapshot(source, output, SCHEMA_PATH)


def test_rejects_existing_temporary_output(tmp_path):
    source = tmp_path / "source.duckdb"
    output = tmp_path / "snapshot.duckdb"
    temporary = output.with_suffix(".duckdb.tmp")
    create_source_database(source)
    temporary.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="temporary snapshot already exists"):
        snapshot.create_snapshot(source, output, SCHEMA_PATH)


def test_removes_temporary_file_when_copy_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.duckdb"
    output = tmp_path / "snapshot.duckdb"
    with duckdb.connect(str(source)) as connection:
        connection.execute("CREATE TABLE runs (run_id VARCHAR)")
    monkeypatch.setattr(snapshot, "_attach_sqlite_source", attach_test_source)

    with pytest.raises(ValueError, match="missing analytical tables"):
        snapshot.create_snapshot(source, output, SCHEMA_PATH)

    assert not output.exists()
    assert not output.with_suffix(".duckdb.tmp").exists()


def test_does_not_replace_existing_link_when_copy_fails(tmp_path, monkeypatch):
    source = tmp_path / "source.duckdb"
    output = tmp_path / "snapshot.duckdb"
    previous = tmp_path / "previous.duckdb"
    link = tmp_path / "current.duckdb"
    previous.write_bytes(b"previous")
    link.symlink_to(previous)
    with duckdb.connect(str(source)) as connection:
        connection.execute("CREATE TABLE runs (run_id VARCHAR)")
    monkeypatch.setattr(snapshot, "_attach_sqlite_source", attach_test_source)

    with pytest.raises(ValueError, match="missing analytical tables"):
        snapshot.create_snapshot(source, output, SCHEMA_PATH, link)

    assert link.resolve() == previous.resolve()


def test_sqlite_attachment_uses_trusted_maintenance_commands(tmp_path):
    source = tmp_path / "source's snapshot.db"
    connection = RecordingConnection()

    snapshot._attach_sqlite_source(connection, source)

    escaped_source = str(source.resolve()).replace("'", "''")
    assert connection.statements == [
        "INSTALL sqlite",
        "LOAD sqlite",
        f"ATTACH '{escaped_source}' AS source_sqlite (TYPE sqlite, READ_ONLY)",
    ]


class RecordingConnection:
    def __init__(self):
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
