import math
import os
from dataclasses import dataclass, field
from typing import Literal, Mapping


Provider = Literal["glm", "sea_lion"]
ThinkingMode = Literal["enabled", "disabled", "provider_default"]

_REQUIRED_VARIABLES = (
    "STS2_LLM_PROVIDER",
    "STS2_LLM_BASE_URL",
    "STS2_LLM_API_KEY",
    "STS2_LLM_MODEL",
    "STS2_LLM_THINKING_MODE",
)


@dataclass(frozen=True)
class ModelSettings:
    provider: Provider
    base_url: str
    api_key: str = field(repr=False)
    model: str
    thinking_mode: ThinkingMode
    temperature: float = 0
    max_tokens: int = 1024
    timeout_seconds: float = 60
    max_retries: int = 2
    disable_provider_cache: bool = True

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> "ModelSettings":
        values = os.environ if environment is None else environment
        required = {
            variable: _required_value(values, variable) for variable in _REQUIRED_VARIABLES
        }

        provider = required["STS2_LLM_PROVIDER"]
        if provider not in {"glm", "sea_lion"}:
            raise ValueError("STS2_LLM_PROVIDER must be one of: glm, sea_lion")

        thinking_mode = required["STS2_LLM_THINKING_MODE"]
        if thinking_mode not in {"enabled", "disabled", "provider_default"}:
            raise ValueError(
                "STS2_LLM_THINKING_MODE must be one of: enabled, disabled, provider_default"
            )

        temperature = _float_value(values, "STS2_LLM_TEMPERATURE", 0)
        if not math.isfinite(temperature):
            raise ValueError("STS2_LLM_TEMPERATURE must be finite")

        max_tokens = _integer_value(values, "STS2_LLM_MAX_TOKENS", 1024)
        if max_tokens < 1:
            raise ValueError("STS2_LLM_MAX_TOKENS must be positive")

        timeout_seconds = _float_value(values, "STS2_LLM_TIMEOUT_SECONDS", 60)
        if not math.isfinite(timeout_seconds):
            raise ValueError("STS2_LLM_TIMEOUT_SECONDS must be finite")
        if timeout_seconds <= 0:
            raise ValueError("STS2_LLM_TIMEOUT_SECONDS must be positive")

        max_retries = _integer_value(values, "STS2_LLM_MAX_RETRIES", 2)
        if max_retries < 0:
            raise ValueError("STS2_LLM_MAX_RETRIES must be non-negative")

        disable_provider_cache = _boolean_value(
            values, "STS2_LLM_DISABLE_PROVIDER_CACHE", True
        )

        return cls(
            provider=provider,
            base_url=required["STS2_LLM_BASE_URL"],
            api_key=required["STS2_LLM_API_KEY"],
            model=required["STS2_LLM_MODEL"],
            thinking_mode=thinking_mode,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            disable_provider_cache=disable_provider_cache,
        )

    def report_values(self) -> dict[str, str | float | int | bool]:
        return {
            "provider": self.provider,
            "base_url": self.base_url,
            "model": self.model,
            "thinking_mode": self.thinking_mode,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "timeout_seconds": self.timeout_seconds,
            "max_retries": self.max_retries,
            "disable_provider_cache": self.disable_provider_cache,
            "n": 1,
            "streaming": False,
        }


def _required_value(values: Mapping[str, str], variable: str) -> str:
    value = values.get(variable, "").strip()
    if not value:
        raise ValueError(f"{variable} is required")
    return value


def _float_value(values: Mapping[str, str], variable: str, default: float) -> float:
    value = values.get(variable)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as error:
        raise ValueError(f"{variable} must be a number") from error


def _integer_value(values: Mapping[str, str], variable: str, default: int) -> int:
    value = values.get(variable)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as error:
        raise ValueError(f"{variable} must be a number") from error


def _boolean_value(values: Mapping[str, str], variable: str, default: bool) -> bool:
    value = values.get(variable)
    if value is None:
        return default
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError(f"{variable} must be true or false")
