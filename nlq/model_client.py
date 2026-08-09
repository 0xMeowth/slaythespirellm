from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from nlq.model_settings import ModelSettings


def provider_extra_body(settings: ModelSettings) -> dict[str, object]:
    body: dict[str, object] = {}

    if settings.provider == "glm":
        if settings.thinking_mode != "provider_default":
            body["thinking"] = {"type": settings.thinking_mode}
        return body

    if settings.thinking_mode != "provider_default":
        body["chat_template_kwargs"] = {
            "enable_thinking": settings.thinking_mode == "enabled"
        }
    if settings.disable_provider_cache:
        body["cache"] = {"no-cache": True}
    return body


def build_chat_model(
    settings: ModelSettings, model_class=ChatOpenAI
) -> BaseChatModel:
    arguments = {
        "model": settings.model,
        "base_url": settings.base_url,
        "api_key": settings.api_key,
        "temperature": settings.temperature,
        "max_tokens": settings.max_tokens,
        "timeout": settings.timeout_seconds,
        "max_retries": settings.max_retries,
        "n": 1,
        "streaming": False,
    }
    extra_body = provider_extra_body(settings)
    if extra_body:
        arguments["extra_body"] = extra_body
    return model_class(**arguments)
