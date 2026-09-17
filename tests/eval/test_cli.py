import json
import os
import subprocess
import sys
from argparse import Namespace
from importlib import import_module
from pathlib import Path

import duckdb

from eval.cases import load_cases
from eval.manifest import create_manifest, write_manifest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
eval_main = import_module("eval.__main__")


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
    with duckdb.connect(str(path)) as connection:
        connection.execute((PROJECT_ROOT / "duckdb_analytics_schema.sql").read_text())
        connection.execute(
            "INSERT INTO runs "
            "(run_id, character, win, was_abandoned, ascension, submitted_at) "
            "VALUES "
            "('run-1', 'SILENT', 0, 0, 0, '2026-06-01'), "
            "('run-2', 'SILENT', 1, 0, 0, '2026-07-29')"
        )


def create_test_manifest(tmp_path):
    database = tmp_path / "snapshot.db"
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_test_database(database)
    manifest = create_manifest(
        dataset_id="test-dataset",
        database=database,
        source_database=source_database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        created_at="2026-08-08T00:00:00+00:00",
    )
    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest, manifest_path)
    return database, manifest_path


def write_cases(path, gold_sql="SELECT COUNT(*) FROM runs"):
    path.write_text(
        json.dumps(
            [
                {
                    "id": "run_count",
                    "question": "How many runs are stored?",
                    "expected_route": "sql",
                    "gold_sql": gold_sql,
                    "comparison": {"mode": "scalar"},
                    "tags": ["runs", "count"],
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
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    create_test_database(database)
    output = tmp_path / "manifest.json"

    completed = run_cli(
        tmp_path,
        "manifest",
        "create",
        "--database",
        "snapshot.db",
        "--source-database",
        "source.db",
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
    assert raw["engine"] == "duckdb"
    assert raw["engine_version"] == duckdb.__version__
    assert raw["source_database_sha256"]
    assert raw["run_count"] == 2
    assert raw["earliest_run_date"] == "2026-06-01"
    assert raw["latest_run_date"] == "2026-07-29"
    assert "Created manifest: " in completed.stdout


def test_snapshot_create_passes_schema_and_link_to_creator(
    tmp_path, monkeypatch, capsys
):
    source = tmp_path / "source.db"
    output = tmp_path / "eval.duckdb"
    schema = tmp_path / "schema.sql"
    link = tmp_path / "current.duckdb"
    captured = {}

    def fake_create_snapshot(source_path, output_path, schema_path, link_path):
        captured["arguments"] = (
            source_path,
            output_path,
            schema_path,
            link_path,
        )
        return {table: 1 for table in eval_main.ANALYTICAL_TABLES}

    monkeypatch.setattr(eval_main, "create_snapshot", fake_create_snapshot)

    eval_main._create_snapshot(
        Namespace(source=source, output=output, schema=schema, link=link)
    )

    assert captured["arguments"] == (source, output, schema, link)
    assert "Created snapshot: " in capsys.readouterr().out


def test_snapshot_parser_accepts_schema_and_link_arguments():
    parser = eval_main._build_parser()

    args = parser.parse_args(
        [
            "snapshot",
            "create",
            "--source",
            "source.db",
            "--output",
            "snapshot.duckdb",
            "--schema",
            "duckdb_analytics_schema.sql",
            "--link",
            "data/current.duckdb",
        ]
    )

    assert args.schema == Path("duckdb_analytics_schema.sql")
    assert args.link == Path("data/current.duckdb")


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
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE changed (value INTEGER)")

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
    assert "run_count: [[2]]" in completed.stdout
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
    assert "gold SQL failed for run_count" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_debug_flag_includes_traceback(tmp_path):
    database, manifest_path = create_test_manifest(tmp_path)
    with duckdb.connect(str(database)) as connection:
        connection.execute("CREATE TABLE changed (value INTEGER)")

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
