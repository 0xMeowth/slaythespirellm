import math
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PipelineSettings:
    max_attempts: int
    query_timeout_seconds: float
    duckdb_memory_limit: str
    duckdb_threads: int


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
    query_timeout_seconds = nlq_settings.get("query_timeout_seconds")
    if (
        not isinstance(query_timeout_seconds, (int, float))
        or isinstance(query_timeout_seconds, bool)
        or not math.isfinite(query_timeout_seconds)
        or query_timeout_seconds <= 0
    ):
        raise ValueError(
            "nlq.query_timeout_seconds must be a finite positive number"
        )
    duckdb_memory_limit = nlq_settings.get("duckdb_memory_limit")
    if (
        not isinstance(duckdb_memory_limit, str)
        or not duckdb_memory_limit.strip()
    ):
        raise ValueError(
            "nlq.duckdb_memory_limit must be a non-empty string"
        )
    duckdb_threads = nlq_settings.get("duckdb_threads")
    if (
        not isinstance(duckdb_threads, int)
        or isinstance(duckdb_threads, bool)
        or duckdb_threads <= 0
    ):
        raise ValueError("nlq.duckdb_threads must be a positive integer")
    return PipelineSettings(
        max_attempts=max_attempts,
        query_timeout_seconds=float(query_timeout_seconds),
        duckdb_memory_limit=duckdb_memory_limit,
        duckdb_threads=duckdb_threads,
    )
