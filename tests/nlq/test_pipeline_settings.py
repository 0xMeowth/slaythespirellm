from dataclasses import FrozenInstanceError

import pytest

from nlq.pipeline_settings import load_pipeline_settings


def test_loads_pipeline_settings_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[nlq]\nmax_attempts = 4\nquery_timeout_seconds = 12.5\n"
    )

    settings = load_pipeline_settings(path)

    assert settings.max_attempts == 4
    assert settings.query_timeout_seconds == 12.5


def test_pipeline_settings_are_immutable(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[nlq]\nmax_attempts = 3\nquery_timeout_seconds = 10.0\n"
    )
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
    path.write_text(content + "query_timeout_seconds = 10.0\n")

    with pytest.raises(ValueError, match="nlq.max_attempts must be a positive integer"):
        load_pipeline_settings(path)


@pytest.mark.parametrize(
    "value",
    ["\"ten\"", "true", "0", "-1", "nan", "inf"],
)
def test_rejects_invalid_query_timeout_configuration(tmp_path, value):
    path = tmp_path / "config.toml"
    path.write_text(
        f"[nlq]\nmax_attempts = 3\nquery_timeout_seconds = {value}\n"
    )

    with pytest.raises(
        ValueError,
        match="nlq.query_timeout_seconds must be a finite positive number",
    ):
        load_pipeline_settings(path)


def test_rejects_missing_query_timeout_configuration(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[nlq]\nmax_attempts = 3\n")

    with pytest.raises(
        ValueError,
        match="nlq.query_timeout_seconds must be a finite positive number",
    ):
        load_pipeline_settings(path)


def test_project_configuration_uses_three_attempts(project_root):
    settings = load_pipeline_settings(project_root / "config.toml")

    assert settings.max_attempts == 3
    assert settings.query_timeout_seconds == 10.0
