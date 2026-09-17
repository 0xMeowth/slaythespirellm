import json
import logging
from dataclasses import replace

import duckdb
import pytest

from eval.models import (
    AgentPrediction,
    AgentRunResult,
    ComparisonSpec,
    DatasetManifest,
    EvalCase,
    RuntimeMetadata,
    ScoredTrial,
    TrialRecord,
)
from eval.run_evaluation import (
    GoldQueryError,
    calculate_metrics,
    collect_trial_records,
    run_evaluation,
    score_trial,
    write_report,
)


class SequencePredictor:
    def __init__(self, results):
        self.results = iter(results)
        self.questions = []

    def predict(self, question):
        self.questions.append(question)
        return next(self.results)


def sql_case(case_id="numbers", tags=("numbers",)):
    return EvalCase(
        id=case_id,
        question="How many numbers are stored?",
        expected_route="sql",
        gold_sql="SELECT COUNT(*) FROM numbers",
        comparison=ComparisonSpec("scalar"),
        tags=tags,
    )


def route_case(route, case_id=None, tags=("router",)):
    return EvalCase(
        id=case_id or f"{route}_case",
        question=f"A question routed to {route}",
        expected_route=route,
        gold_sql=None,
        comparison=None,
        tags=tags,
    )


def agent_result(
    route="sql",
    generated_sql="SELECT COUNT(*) FROM numbers",
    attempts=1,
):
    return AgentRunResult(route, generated_sql, attempts)


def test_collects_three_independent_trials_by_default():
    prediction = agent_result()
    predictor = SequencePredictor([prediction, prediction, prediction])
    clock = iter([1.0, 1.1, 2.0, 2.2, 3.0, 3.3]).__next__

    records = collect_trial_records(sql_case(), predictor, clock=clock)

    assert predictor.questions == ["How many numbers are stored?"] * 3
    assert [record.trial for record in records] == [1, 2, 3]
    assert [record.case_id for record in records] == ["numbers"] * 3
    assert [record.runtime.attempt_count for record in records] == [1, 1, 1]
    assert [record.runtime.latency_ms for record in records] == pytest.approx(
        [100.0, 200.0, 300.0]
    )
    assert len({id(record) for record in records}) == 3


def test_allows_one_trial_for_fast_development():
    predictor = SequencePredictor([agent_result(attempts=2)])

    records = collect_trial_records(sql_case(), predictor, trial_count=1)

    assert len(records) == 1
    assert records[0].runtime.attempt_count == 2


@pytest.mark.parametrize("trial_count", [0, -1])
def test_rejects_invalid_trial_count(trial_count):
    with pytest.raises(ValueError, match="trial_count must be positive"):
        collect_trial_records(sql_case(), SequencePredictor([]), trial_count=trial_count)


@pytest.mark.parametrize("attempt_count", [0, 4])
def test_rejects_attempt_count_outside_limit(attempt_count):
    predictor = SequencePredictor([agent_result(attempts=attempt_count)])

    with pytest.raises(ValueError, match="attempt_count must be between 1 and 3"):
        collect_trial_records(sql_case(), predictor, trial_count=1, max_attempts=3)


def test_scores_equivalent_sql(sample_database):
    record = TrialRecord(
        "numbers",
        1,
        AgentPrediction("sql", "SELECT 3"),
        RuntimeMetadata(1, 10.0),
    )

    scored = score_trial(sql_case(), record, sample_database, timeout_seconds=1.0)

    assert scored.trial_passed
    assert scored.route_correct
    assert scored.sql_execution_succeeded
    assert scored.gold_result == scored.predicted_result
    assert scored.failure_category is None


def test_prediction_execution_error_scores_zero(sample_database, caplog):
    record = TrialRecord(
        "numbers",
        1,
        AgentPrediction("sql", "NOT SQL"),
        RuntimeMetadata(1, 10.0),
    )

    with caplog.at_level(logging.WARNING, logger="eval.run_evaluation"):
        scored = score_trial(sql_case(), record, sample_database, timeout_seconds=1.0)

    assert not scored.trial_passed
    assert scored.sql_execution_succeeded is False
    assert scored.failure_category == "execution_error"
    assert scored.error
    assert "Prediction SQL failed for numbers trial 1" in caplog.text


def test_route_mismatch_skips_sql_execution(sample_database):
    record = TrialRecord(
        "numbers",
        1,
        AgentPrediction("decline", None),
        RuntimeMetadata(1, 10.0),
    )

    scored = score_trial(sql_case(), record, sample_database, timeout_seconds=1.0)

    assert not scored.trial_passed
    assert not scored.route_correct
    assert scored.sql_execution_succeeded is False
    assert scored.failure_category == "route_mismatch"


@pytest.mark.parametrize("route", ["decline", "clarify"])
def test_correct_non_sql_route_passes(route, sample_database):
    case = route_case(route)
    record = TrialRecord(
        case.id,
        1,
        AgentPrediction(route, None),
        RuntimeMetadata(1, 10.0),
    )

    scored = score_trial(case, record, sample_database, timeout_seconds=1.0)

    assert scored.trial_passed
    assert scored.route_correct
    assert scored.sql_execution_succeeded is None


def test_broken_gold_sql_raises_dataset_error(sample_database):
    case = replace(sql_case(), gold_sql="NOT SQL")
    record = TrialRecord(
        case.id,
        1,
        AgentPrediction("sql", "SELECT 3"),
        RuntimeMetadata(1, 10.0),
    )

    with pytest.raises(GoldQueryError, match="numbers"):
        score_trial(case, record, sample_database, timeout_seconds=1.0)


def test_retry_success_preserves_attempt_count(sample_database):
    record = TrialRecord(
        "numbers",
        1,
        AgentPrediction("sql", "SELECT 3"),
        RuntimeMetadata(2, 10.0),
    )

    scored = score_trial(sql_case(), record, sample_database, timeout_seconds=1.0)

    assert scored.trial_passed
    assert scored.runtime.attempt_count == 2


def scored_trial(
    case_id,
    trial,
    tags,
    *,
    trial_passed,
    expected_route="sql",
    route_correct=True,
    sql_execution_succeeded=True,
    attempts=1,
    latency=100.0,
):
    return ScoredTrial(
        case_id=case_id,
        trial=trial,
        tags=tags,
        prediction=AgentPrediction(expected_route, None),
        runtime=RuntimeMetadata(attempts, latency),
        expected_route=expected_route,
        route_correct=route_correct,
        gold_result=None,
        predicted_result=None,
        trial_passed=trial_passed,
        sql_execution_succeeded=sql_execution_succeeded,
        failure_category=None if trial_passed else "execution_error",
        error=None,
    )


def test_calculates_required_metrics():
    trials = [
        scored_trial("sql_a", 1, ("x",), trial_passed=True, latency=100.0),
        scored_trial(
            "sql_a",
            2,
            ("x",),
            trial_passed=False,
            sql_execution_succeeded=False,
            attempts=2,
            latency=200.0,
        ),
        scored_trial(
            "sql_b",
            1,
            ("y",),
            trial_passed=True,
            attempts=2,
            latency=300.0,
        ),
        scored_trial(
            "decline_a",
            1,
            ("x",),
            trial_passed=True,
            expected_route="decline",
            sql_execution_succeeded=None,
            latency=400.0,
        ),
    ]

    metrics, case_level_scores, per_tag = calculate_metrics(trials)

    assert metrics.execution_accuracy == pytest.approx(2 / 3)
    assert metrics.router_accuracy == 1.0
    assert metrics.executable_sql_rate == pytest.approx(2 / 3)
    assert metrics.first_attempt_execution_accuracy == pytest.approx(1 / 3)
    assert metrics.retry_recovery_rate == 0.5
    assert metrics.average_attempt_count == 1.5
    assert metrics.mean_latency_ms == 250.0
    assert metrics.p50_latency_ms == 200.0
    assert metrics.p95_latency_ms == 400.0
    assert [
        (item.case_id, item.passes, item.trials)
        for item in case_level_scores
    ] == [
        ("sql_a", 1, 2),
        ("sql_b", 1, 1),
        ("decline_a", 1, 1),
    ]
    assert per_tag["x"].case_count == 2
    assert per_tag["x"].trial_count == 3
    assert per_tag["x"].pass_rate == pytest.approx(2 / 3)
    assert per_tag["y"].case_count == 1
    assert per_tag["y"].pass_rate == 1.0


def test_retry_recovery_is_none_without_retried_trials():
    trials = [scored_trial("sql_a", 1, ("x",), trial_passed=True)]

    metrics, _, _ = calculate_metrics(trials)

    assert metrics.retry_recovery_rate is None


def test_run_evaluation_continues_after_prediction_error(sample_database, caplog):
    case = sql_case()
    predictor = SequencePredictor(
        [
            agent_result(generated_sql="NOT SQL"),
            agent_result(generated_sql="SELECT 3"),
            agent_result(generated_sql="SELECT 3"),
        ]
    )

    with caplog.at_level(logging.INFO, logger="eval.run_evaluation"):
        report = run_evaluation(
            manifest=sample_manifest(),
            database=sample_database,
            cases=[case],
            predictor=predictor,
            case_set_sha256="case-hash",
            timeout_seconds=1.0,
            run_id="test-run",
            created_at="2026-08-08T00:00:00+00:00",
        )

    assert report.run_id == "test-run"
    assert report.aggregate_metrics.execution_accuracy == pytest.approx(2 / 3)
    assert [trial.trial_passed for trial in report.cases[0].trials] == [
        False,
        True,
        True,
    ]
    assert "Evaluation started: 1 cases, 3 trials each" in caplog.text
    assert "Completed numbers trial 3: passed" in caplog.text
    assert "Evaluation completed: test-run" in caplog.text


def test_writes_report_as_json_atomically(tmp_path, sample_database, caplog):
    report = run_evaluation(
        manifest=sample_manifest(),
        database=sample_database,
        cases=[sql_case()],
        predictor=SequencePredictor(
            [agent_result(), agent_result(), agent_result()]
        ),
        case_set_sha256="case-hash",
        timeout_seconds=1.0,
        run_id="test-run",
        created_at="2026-08-08T00:00:00+00:00",
    )
    path = tmp_path / "reports" / "test-run.json"

    with caplog.at_level(logging.INFO, logger="eval.run_evaluation"):
        write_report(report, path)

    raw = json.loads(path.read_text())
    assert raw["run_id"] == "test-run"
    assert raw["dataset"]["dataset_id"] == "test-dataset"
    assert raw["case_set_sha256"] == "case-hash"
    assert raw["configuration"]["trial_count"] == 3
    assert raw["aggregate_metrics"]["execution_accuracy"] == 1.0
    assert len(raw["cases"][0]["trials"]) == 3
    assert raw["cases"][0]["case_level_score"] == {
        "passes": 3,
        "trials": 3,
    }
    trial = raw["cases"][0]["trials"][0]
    assert set(trial) == {"trial", "prediction", "runtime", "evaluation"}
    assert trial["prediction"]["sql"]
    assert trial["evaluation"]["expected_route"] == "sql"
    assert trial["evaluation"]["trial_passed"] is True
    assert "case_id" not in trial
    assert not path.with_suffix(".json.tmp").exists()
    assert f"Report saved: {path}" in caplog.text


def test_run_validates_gold_before_predictor(sample_database):
    predictor = SequencePredictor([])
    broken_case = replace(sql_case(), gold_sql="NOT SQL")

    with pytest.raises(GoldQueryError, match="numbers"):
        run_evaluation(
            manifest=sample_manifest(),
            database=sample_database,
            cases=[broken_case],
            predictor=predictor,
            case_set_sha256="case-hash",
            timeout_seconds=1.0,
        )

    assert predictor.questions == []


def sample_manifest():
    return DatasetManifest(
        dataset_id="test-dataset",
        engine="duckdb",
        engine_version=duckdb.__version__,
        database="sample.db",
        created_at="2026-08-08T00:00:00+00:00",
        earliest_run_date="2026-06-01",
        latest_run_date="2026-07-29",
        run_count=3,
        database_sha256="database-hash",
        source_database_sha256="source-database-hash",
        schema_git_commit="abc123",
    )
