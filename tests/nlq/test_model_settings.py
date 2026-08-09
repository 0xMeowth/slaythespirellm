import math

import pytest

from nlq.model_settings import ModelSettings


@pytest.fixture
def valid_environment():
    return {
        "STS2_LLM_PROVIDER": "glm",
        "STS2_LLM_BASE_URL": "https://example.test/v1",
        "STS2_LLM_API_KEY": "secret-key",
        "STS2_LLM_MODEL": "glm-4.7",
        "STS2_LLM_THINKING_MODE": "enabled",
    }


def test_loads_defaults(valid_environment):
    settings = ModelSettings.from_environment(valid_environment)

    assert settings.temperature == 0
    assert settings.max_tokens == 1024
    assert settings.timeout_seconds == 60
    assert settings.max_retries == 2
    assert settings.disable_provider_cache is True
    assert "secret-key" not in repr(settings)
    assert "api_key" not in settings.report_values()


def test_loads_overrides(valid_environment):
    valid_environment.update(
        {
            "STS2_LLM_TEMPERATURE": "0.25",
            "STS2_LLM_MAX_TOKENS": "2048",
            "STS2_LLM_TIMEOUT_SECONDS": "30.5",
            "STS2_LLM_MAX_RETRIES": "0",
            "STS2_LLM_DISABLE_PROVIDER_CACHE": "false",
        }
    )

    settings = ModelSettings.from_environment(valid_environment)

    assert settings.report_values() == {
        "provider": "glm",
        "base_url": "https://example.test/v1",
        "model": "glm-4.7",
        "thinking_mode": "enabled",
        "temperature": 0.25,
        "max_tokens": 2048,
        "timeout_seconds": 30.5,
        "max_retries": 0,
        "disable_provider_cache": False,
        "n": 1,
        "streaming": False,
    }


@pytest.mark.parametrize(
    "variable",
    [
        "STS2_LLM_PROVIDER",
        "STS2_LLM_BASE_URL",
        "STS2_LLM_API_KEY",
        "STS2_LLM_MODEL",
        "STS2_LLM_THINKING_MODE",
    ],
)
def test_rejects_missing_required_value(valid_environment, variable):
    del valid_environment[variable]

    with pytest.raises(ValueError, match=f"{variable} is required"):
        ModelSettings.from_environment(valid_environment)


@pytest.mark.parametrize(
    "variable",
    [
        "STS2_LLM_PROVIDER",
        "STS2_LLM_BASE_URL",
        "STS2_LLM_API_KEY",
        "STS2_LLM_MODEL",
        "STS2_LLM_THINKING_MODE",
    ],
)
def test_rejects_blank_required_value(valid_environment, variable):
    valid_environment[variable] = "  "

    with pytest.raises(ValueError, match=f"{variable} is required"):
        ModelSettings.from_environment(valid_environment)


def test_rejects_unknown_provider(valid_environment):
    valid_environment["STS2_LLM_PROVIDER"] = "other"

    with pytest.raises(ValueError, match="STS2_LLM_PROVIDER must be one of: glm, sea_lion"):
        ModelSettings.from_environment(valid_environment)


def test_rejects_unsupported_thinking_mode(valid_environment):
    valid_environment["STS2_LLM_THINKING_MODE"] = "automatic"

    with pytest.raises(
        ValueError,
        match="STS2_LLM_THINKING_MODE must be one of: enabled, disabled, provider_default",
    ):
        ModelSettings.from_environment(valid_environment)


@pytest.mark.parametrize("value", ["TRUE", "False", "yes", "0", ""])
def test_rejects_invalid_cache_boolean(valid_environment, value):
    valid_environment["STS2_LLM_DISABLE_PROVIDER_CACHE"] = value

    with pytest.raises(
        ValueError,
        match="STS2_LLM_DISABLE_PROVIDER_CACHE must be true or false",
    ):
        ModelSettings.from_environment(valid_environment)


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("STS2_LLM_TEMPERATURE", "cold"),
        ("STS2_LLM_MAX_TOKENS", "many"),
        ("STS2_LLM_TIMEOUT_SECONDS", "soon"),
        ("STS2_LLM_MAX_RETRIES", "twice"),
    ],
)
def test_rejects_non_numeric_values(valid_environment, variable, value):
    valid_environment[variable] = value

    with pytest.raises(ValueError, match=f"{variable} must be a number"):
        ModelSettings.from_environment(valid_environment)


@pytest.mark.parametrize("value", ["nan", "inf", "-inf"])
def test_rejects_non_finite_temperature(valid_environment, value):
    valid_environment["STS2_LLM_TEMPERATURE"] = value

    with pytest.raises(ValueError, match="STS2_LLM_TEMPERATURE must be finite"):
        ModelSettings.from_environment(valid_environment)


@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("STS2_LLM_MAX_TOKENS", "0", "STS2_LLM_MAX_TOKENS must be positive"),
        ("STS2_LLM_MAX_TOKENS", "-1", "STS2_LLM_MAX_TOKENS must be positive"),
        (
            "STS2_LLM_TIMEOUT_SECONDS",
            "0",
            "STS2_LLM_TIMEOUT_SECONDS must be positive",
        ),
        (
            "STS2_LLM_TIMEOUT_SECONDS",
            "-0.1",
            "STS2_LLM_TIMEOUT_SECONDS must be positive",
        ),
        ("STS2_LLM_MAX_RETRIES", "-1", "STS2_LLM_MAX_RETRIES must be non-negative"),
    ],
)
def test_rejects_out_of_range_numbers(valid_environment, variable, value, message):
    valid_environment[variable] = value

    with pytest.raises(ValueError, match=message):
        ModelSettings.from_environment(valid_environment)


def test_is_immutable(valid_environment):
    settings = ModelSettings.from_environment(valid_environment)

    with pytest.raises(AttributeError):
        settings.temperature = math.pi
