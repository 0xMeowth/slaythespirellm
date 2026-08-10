import sqlite3
from pathlib import Path

import pytest

from eval.models import DatasetManifest
from nlq.schema_dictionary import SchemaDictionary, load_schema_dictionary


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parents[2]


@pytest.fixture
def analytical_database(tmp_path: Path, project_root: Path) -> Path:
    database = tmp_path / "analytical.db"
    with sqlite3.connect(database) as connection:
        connection.executescript((project_root / "schema.sql").read_text())
    return database


@pytest.fixture
def dictionary(project_root: Path) -> SchemaDictionary:
    return load_schema_dictionary(project_root / "nlq" / "schema_dictionary.json")


@pytest.fixture
def manifest() -> DatasetManifest:
    return DatasetManifest(
        dataset_id="test-dataset",
        database="analytical.db",
        created_at="2026-08-10T00:00:00+00:00",
        earliest_run_date="2026-07-01",
        latest_run_date="2026-08-01",
        run_count=1,
        database_sha256="database-sha256",
        schema_git_commit="abc123",
    )
