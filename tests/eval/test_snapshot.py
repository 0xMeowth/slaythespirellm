import sqlite3

import pytest

from eval.snapshot import ANALYTICAL_TABLES, create_snapshot


def create_source_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE cards (card_id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE relics (relic_id TEXT PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE runs (run_id TEXT PRIMARY KEY, character TEXT NOT NULL);
            CREATE INDEX idx_runs_character ON runs(character);
            CREATE TABLE run_cards (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                card_id TEXT NOT NULL
            );
            CREATE TABLE run_relics (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                relic_id TEXT NOT NULL
            );
            CREATE TABLE run_card_choices (
                run_id TEXT NOT NULL REFERENCES runs(run_id),
                card_id TEXT NOT NULL,
                was_picked INTEGER NOT NULL
            );
            CREATE TABLE raw_runs (run_id TEXT PRIMARY KEY, line_gz BLOB NOT NULL);
            CREATE TABLE sync_state (key TEXT PRIMARY KEY, value TEXT);

            INSERT INTO cards VALUES ('GOLD_AXE', 'Gold Axe');
            INSERT INTO relics VALUES ('FISHING_ROD', 'Fishing Rod');
            INSERT INTO runs VALUES ('run-1', 'SILENT');
            INSERT INTO run_cards VALUES ('run-1', 'GOLD_AXE');
            INSERT INTO run_relics VALUES ('run-1', 'FISHING_ROD');
            INSERT INTO run_card_choices VALUES ('run-1', 'GOLD_AXE', 1);
            INSERT INTO raw_runs VALUES ('run-1', X'00');
            INSERT INTO sync_state VALUES ('cursor', 'next');
            """
        )


def test_creates_snapshot_with_only_analytical_tables(tmp_path):
    source = tmp_path / "source.db"
    output = tmp_path / "snapshot.db"
    create_source_database(source)

    counts = create_snapshot(source, output)

    with sqlite3.connect(output) as connection:
        tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
                )
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        assert connection.execute("SELECT * FROM runs").fetchall() == [
            ("run-1", "SILENT")
        ]

    assert tables == set(ANALYTICAL_TABLES)
    assert "raw_runs" not in tables
    assert "sync_state" not in tables
    assert "idx_runs_character" in indexes
    assert counts == {table: 1 for table in ANALYTICAL_TABLES}


def test_rejects_existing_output(tmp_path):
    source = tmp_path / "source.db"
    output = tmp_path / "snapshot.db"
    create_source_database(source)
    output.write_bytes(b"existing")

    with pytest.raises(FileExistsError, match="already exists"):
        create_snapshot(source, output)


def test_removes_temporary_file_when_copy_fails(tmp_path):
    source = tmp_path / "source.db"
    output = tmp_path / "snapshot.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE runs (run_id TEXT)")

    with pytest.raises(ValueError, match="missing analytical tables"):
        create_snapshot(source, output)

    assert not output.exists()
    assert not output.with_suffix(".db.tmp").exists()
