from dataclasses import FrozenInstanceError

import pytest

from nlq.pipeline_settings import load_pipeline_settings


def _config(
    *,
    max_attempts="4",
    query_timeout_seconds="12.5",
    duckdb_memory_limit='"4GB"',
    duckdb_threads="4",
):
    return (
        "[nlq]\n"
        f"max_attempts = {max_attempts}\n"
        f"query_timeout_seconds = {query_timeout_seconds}\n"
        f"duckdb_memory_limit = {duckdb_memory_limit}\n"
        f"duckdb_threads = {duckdb_threads}\n"
    )


def test_loads_pipeline_settings_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_config())

    settings = load_pipeline_settings(path)

    assert settings.max_attempts == 4
    assert settings.query_timeout_seconds == 12.5
    assert settings.duckdb_memory_limit == "4GB"
    assert settings.duckdb_threads == 4


def test_pipeline_settings_are_immutable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_config(max_attempts="3", query_timeout_seconds="10.0"))
    settings = load_pipeline_settings(path)

    with pytest.raises(FrozenInstanceError):
        settings.max_attempts = 2


@pytest.mark.parametrize(
    "content",
    [
        "max_attempts = 3\n",
        "[nlq]\n",
        '[nlq]\nmax_attempts = "three"\n',
        "[nlq]\nmax_attempts = true\n",
        "[nlq]\nmax_attempts = 0\n",
    ],
)
def test_rejects_invalid_max_attempts_configuration(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(
        content
        + "query_timeout_seconds = 10.0\n"
        + 'duckdb_memory_limit = "4GB"\n'
        + "duckdb_threads = 4\n"
    )

    with pytest.raises(ValueError, match="nlq.max_attempts must be a positive integer"):
        load_pipeline_settings(path)


@pytest.mark.parametrize(
    "value",
    ["\"ten\"", "true", "0", "-1", "nan", "inf"],
)
def test_rejects_invalid_query_timeout_configuration(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(
        _config(max_attempts="3", query_timeout_seconds=value)
    )

    with pytest.raises(
        ValueError,
        match="nlq.query_timeout_seconds must be a finite positive number",
    ):
        load_pipeline_settings(path)


def test_rejects_missing_query_timeout_configuration(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[nlq]\n"
        "max_attempts = 3\n"
        'duckdb_memory_limit = "4GB"\n'
        "duckdb_threads = 4\n"
    )

    with pytest.raises(
        ValueError,
        match="nlq.query_timeout_seconds must be a finite positive number",
    ):
        load_pipeline_settings(path)


@pytest.mark.parametrize("value", ['""', '"   "', "4", "true"])
def test_rejects_invalid_duckdb_memory_limit(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(_config(duckdb_memory_limit=value))

    with pytest.raises(
        ValueError,
        match="nlq.duckdb_memory_limit must be a non-empty string",
    ):
        load_pipeline_settings(path)


def test_rejects_missing_duckdb_memory_limit(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[nlq]\n"
        "max_attempts = 3\n"
        "query_timeout_seconds = 10.0\n"
        "duckdb_threads = 4\n"
    )

    with pytest.raises(
        ValueError,
        match="nlq.duckdb_memory_limit must be a non-empty string",
    ):
        load_pipeline_settings(path)


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"4"'])
def test_rejects_invalid_duckdb_thread_count(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(_config(duckdb_threads=value))

    with pytest.raises(
        ValueError,
        match="nlq.duckdb_threads must be a positive integer",
    ):
        load_pipeline_settings(path)


def test_rejects_missing_duckdb_thread_count(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[nlq]\n"
        "max_attempts = 3\n"
        "query_timeout_seconds = 10.0\n"
        'duckdb_memory_limit = "4GB"\n'
    )

    with pytest.raises(
        ValueError,
        match="nlq.duckdb_threads must be a positive integer",
    ):
        load_pipeline_settings(path)


def test_project_configuration_uses_three_attempts(project_root):
    settings = load_pipeline_settings(project_root / "config.toml")

    assert settings.max_attempts == 3
    assert settings.query_timeout_seconds == 10.0
    assert settings.duckdb_memory_limit == "4GB"
    assert settings.duckdb_threads == 4
