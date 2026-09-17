import json
import os
import subprocess
import sys
import traceback
from pathlib import Path

import duckdb
import pytest
from langchain_core.messages import AIMessage

from eval.manifest import create_manifest, write_manifest
from nlq.__main__ import _reraise_without_api_key, main


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def valid_environment():
    return {
        "STS2_LLM_PROVIDER": "glm",
        "STS2_LLM_BASE_URL": "https://example.test/v1",
        "STS2_LLM_API_KEY": "secret-key",
        "STS2_LLM_MODEL": "glm-4.7",
        "STS2_LLM_THINKING_MODE": "enabled",
    }


@pytest.fixture
def cli_fixture(duckdb_manifest_database: Path, project_root: Path, tmp_path: Path):
    source_database = tmp_path / "source.db"
    source_database.write_bytes(b"source")
    manifest = create_manifest(
        dataset_id="test-dataset",
        database=duckdb_manifest_database,
        source_database=source_database,
        manifest_database_path="analytical.duckdb",
        schema_git_commit="abc123",
        created_at="2026-08-10T00:00:00+00:00",
    )
    manifest_path = tmp_path / "manifest.json"
    write_manifest(manifest, manifest_path)
    return CliFixture(
        working_directory=tmp_path,
        manifest=manifest_path,
        dictionary=project_root / "nlq" / "schema_dictionary.json",
    )


def test_schema_check_prints_verified_metadata_without_context(cli_fixture):
    completed = run_nlq_cli(
        cli_fixture.working_directory,
        "schema",
        "check",
        "--manifest",
        str(cli_fixture.manifest),
        "--dictionary",
        str(cli_fixture.dictionary),
    )

    assert completed.returncode == 0, completed.stderr
    assert "Dataset: test-dataset" in completed.stdout
    assert "Approved tables: runs, run_cards, run_relics, run_card_choices, cards, relics" in completed.stdout
    assert "Context SHA-256:" in completed.stdout
    assert "Context bytes:" in completed.stdout
    assert "Table: runs" not in completed.stdout


def test_schema_check_prints_context_only_when_requested(cli_fixture):
    completed = run_nlq_cli(
        cli_fixture.working_directory,
        "schema",
        "check",
        "--manifest",
        str(cli_fixture.manifest),
        "--dictionary",
        str(cli_fixture.dictionary),
        "--show-context",
    )

    assert completed.returncode == 0, completed.stderr
    assert "Table: runs" in completed.stdout
    assert "Whether the run was won." in completed.stdout


def test_schema_check_reports_dictionary_mismatch_without_traceback(cli_fixture):
    dictionary = json.loads(cli_fixture.dictionary.read_text())
    del dictionary["runs"]["columns"]["run_id"]
    invalid_dictionary = cli_fixture.working_directory / "invalid-dictionary.json"
    invalid_dictionary.write_text(json.dumps(dictionary))

    completed = run_nlq_cli(
        cli_fixture.working_directory,
        "schema",
        "check",
        "--manifest",
        str(cli_fixture.manifest),
        "--dictionary",
        str(invalid_dictionary),
    )

    assert completed.returncode == 1
    assert "error: runs missing dictionary columns: run_id" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_schema_check_reports_manifest_mismatch_without_traceback(cli_fixture):
    with duckdb.connect(
        str(cli_fixture.working_directory / "analytical.duckdb")
    ) as connection:
        connection.execute("CREATE TABLE changed (value TEXT)")

    completed = run_nlq_cli(
        cli_fixture.working_directory,
        "schema",
        "check",
        "--manifest",
        str(cli_fixture.manifest),
        "--dictionary",
        str(cli_fixture.dictionary),
    )

    assert completed.returncode == 1
    assert "error: database checksum mismatch:" in completed.stderr
    assert "Traceback" not in completed.stderr


def test_schema_check_debug_reraises_with_traceback(cli_fixture):
    with duckdb.connect(
        str(cli_fixture.working_directory / "analytical.duckdb")
    ) as connection:
        connection.execute("CREATE TABLE changed (value TEXT)")

    completed = run_nlq_cli(
        cli_fixture.working_directory,
        "--debug",
        "schema",
        "check",
        "--manifest",
        str(cli_fixture.manifest),
        "--dictionary",
        str(cli_fixture.dictionary),
    )

    assert completed.returncode == 1
    assert "Traceback" in completed.stderr
    assert "database checksum mismatch:" in completed.stderr


def test_model_check_uses_injected_fake_and_reports_usage(valid_environment, capsys):
    model = FakeModel(
        AIMessage(
            content="connection-ok",
            usage_metadata={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            additional_kwargs={"reasoning_content": "private reasoning"},
            response_metadata={"headers": {"Authorization": "Bearer secret-key"}},
        )
    )
    model_builder = FakeModelBuilder(model)

    exit_code = main(
        ["model", "check"],
        environment=valid_environment,
        model_builder=model_builder,
        clock=iter([10.0, 10.25]).__next__,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert model.messages == ["Reply with exactly: connection-ok"]
    assert model_builder.settings.provider == "glm"
    assert "Provider: glm" in captured.out
    assert "Model: glm-4.7" in captured.out
    assert "Latency: 250 ms" in captured.out
    assert "Token usage: input=3, output=2, total=5" in captured.out
    assert "secret-key" not in captured.out
    assert "private reasoning" not in captured.out
    assert "Authorization" not in captured.out
    assert captured.err == ""


def test_model_check_omits_usage_when_model_does_not_supply_it(valid_environment, capsys):
    model = FakeModel(AIMessage(content="connection-ok"))

    exit_code = main(
        ["model", "check"],
        environment=valid_environment,
        model_builder=FakeModelBuilder(model),
        clock=iter([1.0, 1.01]).__next__,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Token usage:" not in captured.out


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("", "model returned empty text response"),
        ([], "model returned non-text response"),
    ],
)
def test_model_check_rejects_empty_or_non_text_responses(
    valid_environment, capsys, content, message
):
    model = FakeModel(AIMessage(content=content))

    exit_code = main(
        ["model", "check"],
        environment=valid_environment,
        model_builder=FakeModelBuilder(model),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == f"error: {message}\n"


def test_model_check_redacts_api_key_from_errors_without_debug(valid_environment, capsys):
    model = FakeModel(RuntimeError("request rejected for secret-key"))

    exit_code = main(
        ["model", "check"],
        environment=valid_environment,
        model_builder=FakeModelBuilder(model),
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.err == "error: request rejected for [redacted]\n"
    assert "secret-key" not in captured.err
    assert "Traceback" not in captured.err


def test_model_check_debug_reraises_provider_errors(valid_environment):
    model = FakeModel(RuntimeError("request rejected"))

    with pytest.raises(RuntimeError, match="request rejected"):
        main(
            ["--debug", "model", "check"],
            environment=valid_environment,
            model_builder=FakeModelBuilder(model),
        )


def test_debug_traceback_redacts_api_key_from_chained_cause():
    api_key = "secret-key"
    try:
        try:
            raise ValueError(f"provider details: {api_key}")
        except ValueError as cause:
            raise RuntimeError("safe outer message") from cause
    except RuntimeError as error:
        with pytest.raises(RuntimeError, match="safe outer message") as raised:
            _reraise_without_api_key(error, api_key)

    formatted = "".join(traceback.format_exception(raised.value))

    assert api_key not in formatted
    assert "safe outer message" in formatted
    assert "Traceback" in formatted


def run_nlq_cli(working_directory: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(PROJECT_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "nlq", *arguments],
        cwd=working_directory,
        env=environment,
        capture_output=True,
        text=True,
    )


class CliFixture:
    def __init__(self, working_directory: Path, manifest: Path, dictionary: Path) -> None:
        self.working_directory = working_directory
        self.manifest = manifest
        self.dictionary = dictionary


class FakeModel:
    def __init__(self, response: AIMessage | Exception) -> None:
        self.messages: list[str] = []
        self.response = response

    def invoke(self, message: str) -> AIMessage:
        self.messages.append(message)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


class FakeModelBuilder:
    def __init__(self, model: FakeModel) -> None:
        self.model = model
        self.settings = None

    def __call__(self, settings):
        self.settings = settings
        return self.model
