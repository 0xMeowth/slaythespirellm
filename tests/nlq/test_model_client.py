import pytest

from nlq.model_client import build_chat_model, provider_extra_body
from nlq.model_settings import ModelSettings


class RecordingModel:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


@pytest.fixture
def settings_factory():
    def make_settings(**overrides):
        values = {
            "provider": "glm",
            "base_url": "https://example.test/v1",
            "api_key": "secret-key",
            "model": "test-model",
            "thinking_mode": "enabled",
            "temperature": 0.25,
            "max_tokens": 2048,
            "timeout_seconds": 30.5,
            "max_retries": 1,
            "disable_provider_cache": True,
        }
        values.update(overrides)
        return ModelSettings(**values)

    return make_settings


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("enabled", {"thinking": {"type": "enabled"}}),
        ("disabled", {"thinking": {"type": "disabled"}}),
        ("provider_default", {}),
    ],
)
def test_glm_thinking_body(settings_factory, mode, expected):
    settings = settings_factory(provider="glm", thinking_mode=mode)

    assert provider_extra_body(settings) == expected


@pytest.mark.parametrize(
    ("mode", "expected_thinking"),
    [
        ("enabled", True),
        ("disabled", False),
        ("provider_default", None),
    ],
)
def test_sea_lion_sets_thinking_mode(settings_factory, mode, expected_thinking):
    settings = settings_factory(
        provider="sea_lion", thinking_mode=mode, disable_provider_cache=False
    )

    body = provider_extra_body(settings)

    if expected_thinking is None:
        assert body == {}
    else:
        assert body == {"chat_template_kwargs": {"enable_thinking": expected_thinking}}


def test_sea_lion_disables_thinking_and_cache(settings_factory):
    settings = settings_factory(
        provider="sea_lion",
        thinking_mode="disabled",
        disable_provider_cache=True,
    )

    assert provider_extra_body(settings) == {
        "chat_template_kwargs": {"enable_thinking": False},
        "cache": {"no-cache": True},
    }


def test_provider_extra_body_returns_fresh_dictionary(settings_factory):
    settings = settings_factory(provider="glm", thinking_mode="enabled")

    first_body = provider_extra_body(settings)
    first_body["thinking"]["type"] = "disabled"

    assert provider_extra_body(settings) == {"thinking": {"type": "enabled"}}


def test_builds_model_with_base_arguments(settings_factory):
    settings = settings_factory(
        provider="glm", thinking_mode="provider_default", disable_provider_cache=False
    )

    model = build_chat_model(settings, model_class=RecordingModel)

    assert isinstance(model, RecordingModel)
    assert model.kwargs == {
        "model": "test-model",
        "base_url": "https://example.test/v1",
        "api_key": "secret-key",
        "temperature": 0.25,
        "max_tokens": 2048,
        "timeout": 30.5,
        "max_retries": 1,
        "n": 1,
        "streaming": False,
    }


def test_builds_model_with_provider_extra_body(settings_factory):
    settings = settings_factory(provider="sea_lion", thinking_mode="enabled")

    model = build_chat_model(settings, model_class=RecordingModel)

    assert model.kwargs["extra_body"] == {
        "chat_template_kwargs": {"enable_thinking": True},
        "cache": {"no-cache": True},
    }
