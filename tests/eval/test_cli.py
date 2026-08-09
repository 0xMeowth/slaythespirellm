import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from eval.cases import load_cases
from eval.manifest import create_manifest, write_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def run_cli(working_directory, *arguments):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "eval", *arguments],
        cwd=working_directory,
        env=environment,
        capture_output=True,
        text=True,
    )


def create_test_database(path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE runs (submitted_at TEXT NOT NULL);
            INSERT INTO runs VALUES ('2026-06-01'), ('2026-07-29');
            CREATE TABLE numbers (value INTEGER NOT NULL);
            INSERT INTO numbers VALUES (1), (2), (3);
            """
        )


def create_test_manifest(tmp_path):
    database = tmp_path / "snapshot.db"
    create_test_database(database)
    manifest = create_manifest(
        dataset_id="test-dataset",
        database=database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        run_count=2,
        earliest_run_date="2026-06-01",
        latest_run_date="2026-07-29",
        created_at="2026-08-08T00:00:00+00:00",
    )
    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest, manifest_path)
    return database, manifest_path


def write_cases(path, gold_sql="SELECT COUNT(*) FROM numbers"):
    path.write_text(
        json.dumps(
            [
                {
                    "id": "number_count",
                    "question": "How many numbers are stored?",
                    "expected_route": "sql",
                    "gold_sql": gold_sql,
                    "comparison": {"mode": "scalar"},
                    "tags": ["numbers", "count"],
                },
                {
                    "id": "strategy",
                    "question": "How should I play?",
                    "expected_route": "decline",
                    "tags": ["router"],
                },
            ]
        )
        + "\n"
    )


def test_manifest_create_inspects_database_and_writes_json(tmp_path):
    database = tmp_path / "snapshot.db"
    create_test_database(database)
    output = tmp_path / "manifest.json"

    completed = run_cli(
        tmp_path,
        "manifest",
        "create",
        "--database",
        "snapshot.db",
        "--dataset-id",
        "test-dataset",
        "--schema-git-commit",
        "abc123",
        "--output",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    raw = json.loads(output.read_text())
    assert raw["database"] == "snapshot.db"
    assert raw["run_count"] == 2
    assert raw["earliest_run_date"] == "2026-06-01"
    assert raw["latest_run_date"] == "2026-07-29"
    assert "Created manifest: " in completed.stdout


def test_snapshot_create_writes_compact_database(tmp_path):
    source = tmp_path / "source.db"
    create_test_database(source)
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE cards (card_id TEXT);
            CREATE TABLE relics (relic_id TEXT);
            CREATE TABLE run_cards (run_id TEXT);
            CREATE TABLE run_relics (run_id TEXT);
            CREATE TABLE run_card_choices (run_id TEXT);
            CREATE TABLE raw_runs (line_gz BLOB);
            """
        )
    output = tmp_path / "eval.db"

    completed = run_cli(
        tmp_path,
        "snapshot",
        "create",
        "--source",
        str(source),
        "--output",
        str(output),
    )

    assert completed.returncode == 0, completed.stderr
    assert output.exists()
    assert "Created snapshot: " in completed.stdout
    assert "6 analytical tables" in completed.stdout


def test_manifest_verify_accepts_matching_database(tmp_path):
    _, manifest_path = create_test_manifest(tmp_path)

    completed = run_cli(
        tmp_path,
        "manifest",
        "verify",
        "--manifest",
        str(manifest_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "Verified dataset: test-dataset" in completed.stdout


def test_manifest_verify_reports_checksum_mismatch_without_traceback(tmp_path):
    database, manifest_path = create_test_manifest(tmp_path)
    database.write_bytes(b"changed")

    completed = run_cli(
        tmp_path,
        "manifest",
        "verify",
        "--manifest",
        str(manifest_path),
    )

    assert completed.returncode == 1
    assert "checksum mismatch" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_cases_validate_executes_gold_sql_and_counts_routes(tmp_path):
    _, manifest_path = create_test_manifest(tmp_path)
    cases_path = tmp_path / "cases.json"
    write_cases(cases_path)

    completed = run_cli(
        tmp_path,
        "cases",
        "validate",
        "--cases",
        str(cases_path),
        "--manifest",
        str(manifest_path),
    )

    assert completed.returncode == 0, completed.stderr
    assert "number_count: [[3]]" in completed.stdout
    assert "Validated 1 SQL cases and 1 router cases" in completed.stdout


def test_cases_validate_reports_invalid_gold_sql_without_traceback(tmp_path):
    _, manifest_path = create_test_manifest(tmp_path)
    cases_path = tmp_path / "cases.json"
    write_cases(cases_path, gold_sql="NOT SQL")

    completed = run_cli(
        tmp_path,
        "cases",
        "validate",
        "--cases",
        str(cases_path),
        "--manifest",
        str(manifest_path),
    )

    assert completed.returncode == 1
    assert "gold SQL failed for number_count" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_debug_flag_includes_traceback(tmp_path):
    database, manifest_path = create_test_manifest(tmp_path)
    database.write_bytes(b"changed")

    completed = run_cli(
        tmp_path,
        "--debug",
        "manifest",
        "verify",
        "--manifest",
        str(manifest_path),
    )

    assert completed.returncode == 1
    assert "Traceback" in completed.stderr
    assert "checksum mismatch" in completed.stderr


def test_initial_case_sets_have_expected_sizes():
    development = load_cases(PROJECT_ROOT / "eval/cases/development.json")
    held_out = load_cases(PROJECT_ROOT / "eval/cases/held_out.json")

    assert len(development) == 10
    assert sum(case.expected_route == "sql" for case in development) == 8
    assert sum(case.expected_route != "sql" for case in development) == 2
    assert held_out == []
