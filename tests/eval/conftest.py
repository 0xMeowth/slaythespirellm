from pathlib import Path

import duckdb
import pytest


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parents[2]


@pytest.fixture
def sample_database(tmp_path: Path) -> Path:
    database = tmp_path / "sample.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute(
            """
            CREATE TABLE numbers (label TEXT NOT NULL, value REAL NOT NULL);
            INSERT INTO numbers VALUES ('a', 1.0), ('b', 2.0), ('b', 2.0);
            """
        )
    return database
