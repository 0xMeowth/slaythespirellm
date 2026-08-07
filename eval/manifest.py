import hashlib
import json
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from eval.models import DatasetManifest

MANIFEST_FIELDS = {
    "dataset_id",
    "database",
    "created_at",
    "earliest_run_date",
    "latest_run_date",
    "run_count",
    "database_sha256",
    "schema_git_commit",
}
HASH_CHUNK_SIZE = 8 * 1024 * 1024


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(HASH_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def create_manifest(
    *,
    dataset_id: str,
    database: Path,
    manifest_database_path: str,
    schema_git_commit: str,
    run_count: int,
    earliest_run_date: str,
    latest_run_date: str,
    created_at: str | None = None,
) -> DatasetManifest:
    if Path(manifest_database_path).is_absolute():
        raise ValueError("manifest database path must be relative")
    if run_count < 1:
        raise ValueError("run_count must be positive")

    return DatasetManifest(
        dataset_id=_nonempty(dataset_id, "dataset_id"),
        database=_nonempty(manifest_database_path, "database"),
        created_at=created_at or datetime.now(UTC).isoformat(),
        earliest_run_date=_nonempty(earliest_run_date, "earliest_run_date"),
        latest_run_date=_nonempty(latest_run_date, "latest_run_date"),
        run_count=run_count,
        database_sha256=sha256_file(database),
        schema_git_commit=_nonempty(schema_git_commit, "schema_git_commit"),
    )


def inspect_runs(database: Path) -> tuple[int, str, str]:
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        row = connection.execute(
            "SELECT COUNT(*), MIN(submitted_at), MAX(submitted_at) FROM runs"
        ).fetchone()
    if row is None or row[0] == 0 or row[1] is None or row[2] is None:
        raise ValueError("runs table has no dated rows")
    return int(row[0]), str(row[1]), str(row[2])


def write_manifest(manifest: DatasetManifest, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(manifest), indent=2) + "\n")


def load_manifest(path: Path) -> DatasetManifest:
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("manifest must contain a JSON object")

    unknown_fields = set(raw) - MANIFEST_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown manifest fields: {names}")
    missing_fields = MANIFEST_FIELDS - set(raw)
    if missing_fields:
        names = ", ".join(sorted(missing_fields))
        raise ValueError(f"missing manifest fields: {names}")

    database = raw["database"]
    if not isinstance(database, str) or Path(database).is_absolute():
        raise ValueError("manifest database path must be relative")
    if isinstance(raw["run_count"], bool) or not isinstance(raw["run_count"], int):
        raise ValueError("run_count must be an integer")

    text_fields = MANIFEST_FIELDS - {"run_count"}
    for field in text_fields:
        if not isinstance(raw[field], str) or not raw[field].strip():
            raise ValueError(f"{field} must be a non-empty string")

    return DatasetManifest(**raw)


def verify_manifest(manifest: DatasetManifest, project_root: Path) -> Path:
    database = (project_root / manifest.database).resolve()
    if not database.exists():
        raise FileNotFoundError(f"database not found: {manifest.database}")
    actual_sha256 = sha256_file(database)
    if actual_sha256 != manifest.database_sha256:
        raise ValueError(
            "database checksum mismatch: "
            f"expected {manifest.database_sha256}, got {actual_sha256}"
        )
    return database


def _nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value
