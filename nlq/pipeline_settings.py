import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineSettings:
    max_attempts: int


def load_pipeline_settings(path: Path) -> PipelineSettings:
    with path.open("rb") as config_file:
        document = tomllib.load(config_file)
    nlq_settings = document.get("nlq")
    if not isinstance(nlq_settings, dict):
        raise ValueError("nlq.max_attempts must be a positive integer")
    max_attempts = nlq_settings.get("max_attempts")
    if (
        not isinstance(max_attempts, int)
        or isinstance(max_attempts, bool)
        or max_attempts <= 0
    ):
        raise ValueError("nlq.max_attempts must be a positive integer")
    return PipelineSettings(max_attempts=max_attempts)
