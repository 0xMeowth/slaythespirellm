from dataclasses import dataclass
from typing import Literal, Protocol

Route = Literal["sql", "decline", "clarify"]
ComparisonMode = Literal["scalar", "ordered_rows", "unordered_rows"]


@dataclass(frozen=True)
class ComparisonSpec:
    mode: ComparisonMode
    float_tolerance: float = 0.0001


@dataclass(frozen=True)
class EvalCase:
    id: str
    question: str
    expected_route: Route
    gold_sql: str | None
    comparison: ComparisonSpec | None
    tags: tuple[str, ...]


@dataclass(frozen=True)
class AgentPrediction:
    route: Route
    sql: str | None


@dataclass(frozen=True)
class PredictionOutcome:
    prediction: AgentPrediction
    attempt_count: int


class Predictor(Protocol):
    def predict(self, question: str) -> PredictionOutcome: ...


@dataclass(frozen=True)
class RuntimeMetadata:
    attempt_count: int
    latency_ms: float


@dataclass(frozen=True)
class TrialRecord:
    case_id: str
    trial: int
    prediction: AgentPrediction
    runtime: RuntimeMetadata


@dataclass(frozen=True)
class QueryResult:
    column_count: int
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class ResultSummary:
    row_count: int
    sha256: str
    preview: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class ComparisonResult:
    passed: bool
    failure_category: str | None


@dataclass(frozen=True)
class ScoredTrial:
    case_id: str
    trial: int
    tags: tuple[str, ...]
    prediction: AgentPrediction
    runtime: RuntimeMetadata
    expected_route: Route
    route_correct: bool
    gold_result: ResultSummary | None
    predicted_result: ResultSummary | None
    passed: bool
    executable: bool | None
    failure_category: str | None
    error: str | None
