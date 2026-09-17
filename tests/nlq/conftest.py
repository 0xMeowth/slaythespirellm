from pathlib import Path

import duckdb
import pytest

from eval.models import DatasetManifest
from nlq.schema_dictionary import SchemaDictionary, load_schema_dictionary


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).parents[2]


@pytest.fixture
def analytical_database(duckdb_analytical_database: Path) -> Path:
    return duckdb_analytical_database


@pytest.fixture
def duckdb_analytical_database(tmp_path: Path, project_root: Path) -> Path:
    database = tmp_path / "analytical.duckdb"
    with duckdb.connect(str(database)) as connection:
        connection.execute((project_root / "duckdb_analytics_schema.sql").read_text())
    return database


@pytest.fixture
def duckdb_manifest_database(
    duckdb_analytical_database: Path,
) -> Path:
    with duckdb.connect(str(duckdb_analytical_database)) as connection:
        connection.execute(
            "INSERT INTO runs "
            "(run_id, character, win, was_abandoned, ascension, submitted_at) "
            "VALUES ('fixture-run', 'SILENT', 0, 0, 0, '2026-07-01')"
        )
    return duckdb_analytical_database


@pytest.fixture
def dictionary(project_root: Path) -> SchemaDictionary:
    return load_schema_dictionary(project_root / "nlq" / "schema_dictionary.json")


@pytest.fixture
def manifest() -> DatasetManifest:
    return DatasetManifest(
        dataset_id="test-dataset",
        engine="duckdb",
        engine_version=duckdb.__version__,
        database="analytical.db",
        created_at="2026-08-10T00:00:00+00:00",
        earliest_run_date="2026-07-01",
        latest_run_date="2026-08-01",
        run_count=1,
        database_sha256="database-sha256",
        source_database_sha256="source-database-sha256",
        schema_git_commit="abc123",
    )
