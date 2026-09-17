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
class AgentRunResult:
    route: Route
    generated_sql: str | None
    attempt_count: int


class Predictor(Protocol):
    def predict(self, question: str) -> AgentRunResult: ...


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
class DatasetManifest:
    dataset_id: str
    engine: str
    engine_version: str
    database: str
    created_at: str
    earliest_run_date: str
    latest_run_date: str
    run_count: int
    database_sha256: str
    source_database_sha256: str
    schema_git_commit: str


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
    trial_passed: bool
    sql_execution_succeeded: bool | None
    failure_category: str | None
    error: str | None


@dataclass(frozen=True)
class AggregateMetrics:
    execution_accuracy: float
    router_accuracy: float
    executable_sql_rate: float
    first_attempt_execution_accuracy: float
    retry_recovery_rate: float | None
    average_attempt_count: float
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float


@dataclass(frozen=True)
class CaseLevelScore:
    case_id: str
    passes: int
    trials: int


@dataclass(frozen=True)
class TagMetrics:
    case_count: int
    trial_count: int
    pass_rate: float


@dataclass(frozen=True)
class CaseEvaluation:
    case_id: str
    tags: tuple[str, ...]
    case_level_score: CaseLevelScore
    trials: tuple[ScoredTrial, ...]


@dataclass(frozen=True)
class EvaluationReport:
    run_id: str
    created_at: str
    dataset: DatasetManifest
    case_set_sha256: str
    configuration: dict[str, object]
    aggregate_metrics: AggregateMetrics
    per_tag_metrics: dict[str, TagMetrics]
    cases: tuple[CaseEvaluation, ...]
