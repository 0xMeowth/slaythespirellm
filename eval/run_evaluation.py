import json
import logging
import math
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean

from eval.compare import compare_results, summarize_result
from eval.models import (
    AgentPrediction,
    AggregateMetrics,
    CaseEvaluation,
    CaseLevelScore,
    DatasetManifest,
    EvalCase,
    EvaluationReport,
    Predictor,
    RuntimeMetadata,
    ScoredTrial,
    TagMetrics,
    TrialRecord,
)
from eval.query_execution import QueryExecutionError, execute_query

logger = logging.getLogger(__name__)


# A broken gold query is an eval-data defect, not a model failure.
class GoldQueryError(Exception):
    pass


def collect_trial_records(
    case: EvalCase,
    predictor: Predictor,
    trial_count: int = 3,
    max_attempts: int = 3,
    clock: Callable[[], float] = time.perf_counter,
) -> list[TrialRecord]:
    """Collect agent predictions and runtime metadata without executing SQL."""
    if trial_count < 1:
        raise ValueError("trial_count must be positive")
    if max_attempts < 1:
        raise ValueError("max_attempts must be positive")

    records = []
    for trial in range(1, trial_count + 1):
        started = clock()
        agent_result = predictor.predict(case.question)
        elapsed_ms = (clock() - started) * 1_000
        if not 1 <= agent_result.attempt_count <= max_attempts:
            raise ValueError(
                f"attempt_count must be between 1 and {max_attempts}"
            )
        records.append(
            TrialRecord(
                case_id=case.id,
                trial=trial,
                prediction=AgentPrediction(
                    route=agent_result.route,
                    sql=agent_result.generated_sql,
                ),
                runtime=RuntimeMetadata(agent_result.attempt_count, elapsed_ms),
            )
        )
    return records


def score_trial(
    case: EvalCase,
    record: TrialRecord,
    database: Path,
    timeout_seconds: float,
    preview_limit: int = 5,
) -> ScoredTrial:
    if record.case_id != case.id:
        raise ValueError("trial record case_id does not match eval case")

    route_correct = record.prediction.route == case.expected_route
    if not route_correct:
        sql_execution_succeeded = False if case.expected_route == "sql" else None
        return _scored(
            case,
            record,
            route_correct=False,
            trial_passed=False,
            sql_execution_succeeded=sql_execution_succeeded,
            failure_category="route_mismatch",
        )

    if case.expected_route != "sql":
        return _scored(
            case,
            record,
            route_correct=True,
            trial_passed=True,
            sql_execution_succeeded=None,
        )

    if not record.prediction.sql:
        return _scored(
            case,
            record,
            route_correct=True,
            trial_passed=False,
            sql_execution_succeeded=False,
            failure_category="missing_prediction",
        )

    if case.gold_sql is None or case.comparison is None:
        raise GoldQueryError(f"missing gold SQL or comparison for {case.id}")

    try:
        gold = execute_query(database, case.gold_sql, timeout_seconds)
    except QueryExecutionError as error:
        raise GoldQueryError(f"gold SQL failed for {case.id}: {error}") from error

    gold_summary = summarize_result(
        gold,
        case.comparison.mode,
        preview_limit,
    )
    try:
        predicted = execute_query(database, record.prediction.sql, timeout_seconds)
    except QueryExecutionError as error:
        logger.warning(
            "Prediction SQL failed for %s trial %d: %s",
            case.id,
            record.trial,
            error,
        )
        return _scored(
            case,
            record,
            route_correct=True,
            gold_result=gold_summary,
            trial_passed=False,
            sql_execution_succeeded=False,
            failure_category=error.category,
            error=str(error),
        )

    predicted_summary = summarize_result(
        predicted,
        case.comparison.mode,
        preview_limit,
    )
    comparison = compare_results(gold, predicted, case.comparison)
    return _scored(
        case,
        record,
        route_correct=True,
        gold_result=gold_summary,
        predicted_result=predicted_summary,
        trial_passed=comparison.passed,
        sql_execution_succeeded=True,
        failure_category=comparison.failure_category,
    )


def calculate_metrics(
    trials: Iterable[ScoredTrial],
) -> tuple[AggregateMetrics, tuple[CaseLevelScore, ...], dict[str, TagMetrics]]:
    trial_list = list(trials)
    if not trial_list:
        raise ValueError("at least one scored trial is required")

    sql_trials = [trial for trial in trial_list if trial.expected_route == "sql"]
    if not sql_trials:
        execution_accuracy = 0.0
        executable_sql_rate = 0.0
        first_attempt_accuracy = 0.0
    else:
        denominator = len(sql_trials)
        execution_accuracy = sum(
            trial.trial_passed for trial in sql_trials
        ) / denominator
        executable_sql_rate = sum(
            trial.sql_execution_succeeded is True for trial in sql_trials
        ) / denominator
        first_attempt_accuracy = sum(
            trial.trial_passed and trial.runtime.attempt_count == 1
            for trial in sql_trials
        ) / denominator

    retried_trials = [
        trial for trial in sql_trials if trial.runtime.attempt_count > 1
    ]
    retry_recovery_rate = (
        sum(trial.trial_passed for trial in retried_trials) / len(retried_trials)
        if retried_trials
        else None
    )
    latencies = sorted(trial.runtime.latency_ms for trial in trial_list)
    metrics = AggregateMetrics(
        execution_accuracy=execution_accuracy,
        router_accuracy=sum(trial.route_correct for trial in trial_list)
        / len(trial_list),
        executable_sql_rate=executable_sql_rate,
        first_attempt_execution_accuracy=first_attempt_accuracy,
        retry_recovery_rate=retry_recovery_rate,
        average_attempt_count=mean(
            trial.runtime.attempt_count for trial in trial_list
        ),
        mean_latency_ms=mean(latencies),
        p50_latency_ms=_nearest_rank(latencies, 0.50),
        p95_latency_ms=_nearest_rank(latencies, 0.95),
    )
    return (
        metrics,
        _calculate_case_level_scores(trial_list),
        _calculate_tag_metrics(trial_list),
    )


def run_evaluation(
    *,
    manifest: DatasetManifest,
    database: Path,
    cases: list[EvalCase],
    predictor: Predictor,
    case_set_sha256: str,
    trial_count: int = 3,
    max_attempts: int = 3,
    timeout_seconds: float = 5.0,
    preview_limit: int = 5,
    run_id: str | None = None,
    created_at: str | None = None,
    clock: Callable[[], float] = time.perf_counter,
) -> EvaluationReport:
    if not cases:
        raise ValueError("at least one eval case is required")

    logger.info(
        "Evaluation started: %d cases, %d trials each",
        len(cases),
        trial_count,
    )
    _validate_gold_cases(cases, database, timeout_seconds)
    scored_trials = []
    for case in cases:
        records = collect_trial_records(
            case,
            predictor,
            trial_count=trial_count,
            max_attempts=max_attempts,
            clock=clock,
        )
        for record in records:
            scored = score_trial(
                case,
                record,
                database,
                timeout_seconds,
                preview_limit,
            )
            scored_trials.append(scored)
            status = (
                "passed" if scored.trial_passed else scored.failure_category
            )
            logger.info(
                "Completed %s trial %d: %s",
                case.id,
                record.trial,
                status,
            )

    aggregate, case_level_scores, per_tag = calculate_metrics(scored_trials)
    case_level_scores_by_id = {
        item.case_id: item for item in case_level_scores
    }
    trials_by_case = {
        case.id: tuple(
            trial for trial in scored_trials if trial.case_id == case.id
        )
        for case in cases
    }
    timestamp = created_at or datetime.now(UTC).isoformat()
    report = EvaluationReport(
        run_id=run_id or timestamp.replace(":", "").replace("+00:00", "Z"),
        created_at=timestamp,
        dataset=manifest,
        case_set_sha256=case_set_sha256,
        configuration={
            "trial_count": trial_count,
            "max_attempts": max_attempts,
            "timeout_seconds": timeout_seconds,
            "preview_limit": preview_limit,
            "cache_policy": "disabled",
        },
        aggregate_metrics=aggregate,
        per_tag_metrics=per_tag,
        cases=tuple(
            CaseEvaluation(
                case_id=case.id,
                tags=case.tags,
                case_level_score=case_level_scores_by_id[case.id],
                trials=trials_by_case[case.id],
            )
            for case in cases
        ),
    )
    logger.info("Evaluation completed: %s", report.run_id)
    return report


def write_report(report: EvaluationReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    payload = _report_payload(report)
    temporary.write_text(json.dumps(payload, indent=2) + "\n")
    temporary.replace(path)
    logger.info("Report saved: %s", path)


def _scored(
    case: EvalCase,
    record: TrialRecord,
    *,
    route_correct: bool,
    trial_passed: bool,
    sql_execution_succeeded: bool | None,
    gold_result=None,
    predicted_result=None,
    failure_category: str | None = None,
    error: str | None = None,
) -> ScoredTrial:
    return ScoredTrial(
        case_id=case.id,
        trial=record.trial,
        tags=case.tags,
        prediction=record.prediction,
        runtime=record.runtime,
        expected_route=case.expected_route,
        route_correct=route_correct,
        gold_result=gold_result,
        predicted_result=predicted_result,
        trial_passed=trial_passed,
        sql_execution_succeeded=sql_execution_succeeded,
        failure_category=failure_category,
        error=error,
    )


def _validate_gold_cases(
    cases: list[EvalCase],
    database: Path,
    timeout_seconds: float,
) -> None:
    for case in cases:
        if case.expected_route != "sql":
            continue
        if case.gold_sql is None or case.comparison is None:
            raise GoldQueryError(f"missing gold SQL or comparison for {case.id}")
        try:
            execute_query(database, case.gold_sql, timeout_seconds)
        except QueryExecutionError as error:
            logger.error("Gold SQL failed for %s: %s", case.id, error)
            raise GoldQueryError(f"gold SQL failed for {case.id}: {error}") from error


def _calculate_case_level_scores(
    trials: list[ScoredTrial],
) -> tuple[CaseLevelScore, ...]:
    case_ids = dict.fromkeys(trial.case_id for trial in trials)
    return tuple(
        CaseLevelScore(
            case_id=case_id,
            passes=sum(
                trial.trial_passed
                for trial in trials
                if trial.case_id == case_id
            ),
            trials=sum(trial.case_id == case_id for trial in trials),
        )
        for case_id in case_ids
    )


def _calculate_tag_metrics(trials: list[ScoredTrial]) -> dict[str, TagMetrics]:
    tags = sorted({tag for trial in trials for tag in trial.tags})
    metrics = {}
    for tag in tags:
        tagged = [trial for trial in trials if tag in trial.tags]
        metrics[tag] = TagMetrics(
            case_count=len({trial.case_id for trial in tagged}),
            trial_count=len(tagged),
            pass_rate=sum(trial.trial_passed for trial in tagged) / len(tagged),
        )
    return metrics


def _nearest_rank(values: list[float], percentile: float) -> float:
    return values[math.ceil(percentile * len(values)) - 1]


def _report_payload(report: EvaluationReport) -> dict[str, object]:
    return {
        "run_id": report.run_id,
        "created_at": report.created_at,
        "dataset": _json_value(report.dataset),
        "case_set_sha256": report.case_set_sha256,
        "configuration": _json_value(report.configuration),
        "aggregate_metrics": _json_value(report.aggregate_metrics),
        "per_tag_metrics": _json_value(report.per_tag_metrics),
        "cases": [
            {
                "case_id": case.case_id,
                "tags": list(case.tags),
                "case_level_score": {
                    "passes": case.case_level_score.passes,
                    "trials": case.case_level_score.trials,
                },
                "trials": [_trial_payload(trial) for trial in case.trials],
            }
            for case in report.cases
        ],
    }


def _trial_payload(trial: ScoredTrial) -> dict[str, object]:
    return {
        "trial": trial.trial,
        "prediction": _json_value(trial.prediction),
        "runtime": _json_value(trial.runtime),
        "evaluation": {
            "expected_route": trial.expected_route,
            "route_correct": trial.route_correct,
            "gold_result": _json_value(trial.gold_result),
            "predicted_result": _json_value(trial.predicted_result),
            "trial_passed": trial.trial_passed,
            "sql_execution_succeeded": trial.sql_execution_succeeded,
            "failure_category": trial.failure_category,
            "error": trial.error,
        },
    }


def _json_value(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, bytes):
        return {"type": "bytes", "hex": value.hex()}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported report value: {type(value).__name__}")
