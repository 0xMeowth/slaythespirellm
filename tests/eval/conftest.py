import sqlite3
from pathlib import Path

import pytest


@pytest.fixture
def sample_database(tmp_path: Path) -> Path:
    database = tmp_path / "sample.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE numbers (label TEXT NOT NULL, value REAL NOT NULL);
            INSERT INTO numbers VALUES ('a', 1.0), ('b', 2.0), ('b', 2.0);
            """
        )
    return database
