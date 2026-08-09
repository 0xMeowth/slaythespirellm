# Phase 3a — Evaluation System

## Purpose

Build a deterministic evaluation system for the StS2 text-to-SQL pipeline before
tuning prompts or comparing models. The evaluator measures whether SQL generated at
runtime produces the same database result as a human-verified reference query.

The evaluator does not optimize prompts, call DSPy, or judge natural-language wording.
It provides the scoring foundation those systems can use later.

## Scope

Phase 3a includes:

- A versioned manifest for the frozen SQLite evaluation database.
- Development and held-out question sets.
- Human-verified gold SQL for SQL-answerable questions.
- Three independent prediction trials per case through a predictor interface.
- Execution-based result comparison.
- Router and SQL-generation metrics.
- Machine-readable and console reports.

Phase 3a excludes:

- The production LangGraph pipeline, implemented in Phase 3d.
- Langfuse integration, implemented in Phase 3g.
- DSPy or other prompt optimization.
- Production response caching.
- Evaluation of strategy or advice answer quality.

## Terminology

- **Eval case:** one question with its expected route and, when applicable, gold SQL.
- **Gold SQL:** a complete, human-verified reference query that answers the question.
- **Prediction:** the route and SQL produced by the NLQ pipeline for one trial.
- **Trial:** one independent invocation of the configured prediction provider for one
  eval case. Fixtures provide predictions during evaluator development; the complete
  NLQ pipeline becomes the provider after Phase 3d.
- **Attempt:** one SQL-generation attempt inside a trial. A trial may contain up to
  three attempts when validation or execution errors trigger correction.
- **Development set:** cases used while changing prompts, models, examples,
  thresholds, and graph behavior.
- **Held-out set:** fixed cases reserved for formal comparisons and not used to guide
  development decisions.

## Files

```text
docs/specs/phase-3a-evaluation.md
eval/datasets/manifest.json
eval/cases/development.json
eval/cases/held_out.json
eval/reports/<run-id>.json
```

The initial implementation supports 10 cases to validate the complete evaluation
flow. The dataset then expands toward roughly 40 cases before final model comparisons.

## Frozen Database Manifest

The initial frozen evaluation snapshot is a compact copy of the ingestion database.
It contains only `runs`, `run_cards`, `run_relics`, `run_card_choices`, `cards`, and
`relics`, preserving their indexes. It excludes `raw_runs`, `sync_state`, and
`sync_log`. Ingestion must never modify the frozen file; future syncs continue against
the separate live database.

`eval/datasets/manifest.json` records:

```json
{
  "dataset_id": "sts2-2026-07-29",
  "database": "data/spire_eval_2026-07-29.db",
  "created_at": "<ISO-8601 timestamp>",
  "earliest_run_date": "2026-06-01",
  "latest_run_date": "2026-07-29",
  "run_count": 659515,
  "database_sha256": "<SHA-256 of the SQLite file>",
  "schema_git_commit": "<Git commit containing the schema version>"
}
```

The evaluator verifies `database_sha256` once before a run. A mismatch aborts the
entire run because scores from different data snapshots are not directly comparable.
The manifest is committed to Git; the database remains local and gitignored.

## Dataset Separation

`development.json` contains cases used during normal iteration. Its results may guide
changes to prompts, model settings, entity-linking thresholds, examples, and graph
logic.

`held_out.json` contains cases used only for formal comparison. The held-out set stays
fixed so scores remain comparable. If its failures are repeatedly inspected and used
to change the system, it has become development data and must be replaced with a new
unseen held-out set.

Held-out questions and gold SQL must never enter the prompt, example library,
retrieval index, conversation memory, or agent tools. The evaluator may read them.

## Case Format

SQL-answerable case:

```json
{
  "id": "stored_run_count",
  "question": "How many runs are stored?",
  "expected_route": "sql",
  "gold_sql": "SELECT COUNT(*) FROM runs",
  "comparison": {
    "mode": "scalar",
    "float_tolerance": 0.0001
  },
  "tags": ["runs", "count", "single-table"]
}
```

Router-only case:

```json
{
  "id": "silent_strategy_advice",
  "question": "How should I play the Silent?",
  "expected_route": "decline",
  "tags": ["router", "strategy"]
}
```

Required fields:

- `id`: stable unique identifier.
- `question`: user-facing natural-language question.
- `expected_route`: `sql`, `decline`, or `clarify`.
- `gold_sql`: required only when `expected_route` is `sql`.
- `comparison`: required only for SQL cases.
- `tags`: diagnostic categories used for per-category reporting.

The case schema belongs to this project. Langfuse and other libraries do not assign
meaning to these keys; the custom evaluator consumes them.

## Gold SQL Rules

Gold SQL must:

- Be a complete SQLite query, not a saved answer.
- Execute successfully against the frozen database.
- Answer the natural-language question as written.
- Use only approved analytical tables.
- Be manually reviewed before a case is accepted.

Different SQL queries can be semantically equivalent. Generated SQL does not need to
match the gold SQL text or structure. It passes when both queries produce equivalent
results under the case's comparison rules.

A failing gold query is a dataset defect and aborts that case. It must not count as a
model failure.

## Agent Output and Trial Records

The NLQ agent outputs only its decision and final generated SQL:

```json
{
  "route": "sql",
  "sql": "SELECT COUNT(*) FROM runs"
}
```

The agent does not generate the case ID, trial number, attempt count, or latency. The
eval runner already knows the case and trial, measures elapsed time, and receives the
attempt count from pipeline state. It combines those values with the agent output to
form the intermediate trial record consumed by the deterministic scorer:

```json
[
  {
    "case_id": "stored_run_count",
    "trial": 1,
    "prediction": {
      "route": "sql",
      "sql": "SELECT COUNT(*) FROM runs"
    },
    "runtime": {
      "attempt_count": 1,
      "latency_ms": 820
    }
  },
  {
    "case_id": "stored_run_count",
    "trial": 2,
    "prediction": {
      "route": "sql",
      "sql": "SELECT COUNT(*) FROM runs"
    },
    "runtime": {
      "attempt_count": 1,
      "latency_ms": 790
    }
  },
  {
    "case_id": "stored_run_count",
    "trial": 3,
    "prediction": {
      "route": "sql",
      "sql": "SELECT COUNT(*) FROM runs"
    },
    "runtime": {
      "attempt_count": 2,
      "latency_ms": 1430
    }
  }
]
```

These are three trial records for one case: one final agent prediction plus runtime
metadata from each independent trial. They are flat records keyed by `case_id` and
`trial`, not nested fields such as `trial_1`. A trial may internally make several SQL
attempts, but contributes one final trial record.

This interface keeps scoring independent from LangGraph. During Phase 3a, fixtures can
exercise the evaluator. Once the NLQ pipeline exists, an adapter supplies real
trial records using the same format.

The data flow is:

```text
case JSON → eval runner → NLQ agent output
                         + runner runtime metadata
                         → trial record → deterministic scorer → scored report
```

## Trial Policy

Every formal evaluation runs each case three times. Each trial:

- Starts with fresh predictor state and no conversation history. The LangGraph adapter
  creates fresh graph state once it exists.
- Makes an independent model request.
- Disables application response caches and semantic caches.
- May use up to three internal SQL-generation attempts when the LangGraph correction
  loop is available.
- Produces its own prediction, timing, attempt count, and score.

`max_attempts` is pipeline configuration, defaulting to three. `attempt_count` records
how many attempts a specific trial actually used. Reaching the maximum fails that
trial after the final unsuccessful attempt; it does not abort the remaining eval run.

For 10 cases, a formal run contains 30 independent trials. A trial that succeeds after
an internal correction passes execution accuracy but does not pass first-attempt
accuracy.

Development commands may run a single trial for faster debugging, but three-trial
execution is a required evaluator capability and the default for reported experiments.
Formal results must never select the best of three trials.

## Cache Policy

Evaluation disables caches that can return a previous model response or generated SQL.
Otherwise repeated trials are not independent.

Allowed caches:

- Provider prompt-prefix caching that only reuses model computation.
- SQLite page caching that only speeds database reads.

Disallowed caches:

- LangChain or application response caches.
- Semantic caches that reuse answers for similar questions.
- Conversation or agent memory shared between trials.

Production caching is outside Phase 3a. The initial production design should use exact
normalized-question caching keyed by dataset version and pipeline version. Semantic
caching requires a separate accuracy and invalidation design.

## Execution and Comparison

For every SQL prediction, the evaluator:

1. Opens the frozen SQLite database read-only.
2. Executes the gold SQL.
3. Executes the generated SQL with a timeout.
4. Fetches both result tables.
5. Normalizes SQLite values for comparison.
6. Applies the case's comparison mode.
7. Records pass, failure details, and timing.

Comparison modes:

- `scalar`: compare one returned value.
- `ordered_rows`: compare row and column shape, values, duplicate counts, and order.
- `unordered_rows`: compare row and column shape, values, and duplicate counts while
  ignoring row order.

Column aliases are ignored. Extra or missing rows or columns fail. Numeric values use
the case's absolute floating-point tolerance. Non-numeric values require exact equality
after SQLite type normalization.

Execution equivalence is the primary correctness rule. Soft result F1 may be recorded
as a diagnostic score but does not turn a failed exact comparison into a pass.

## Router Evaluation

The evaluator compares the prediction's `route` with `expected_route`.

- An expected `sql` case routed elsewhere fails router accuracy and does not receive
  execution credit.
- An expected `decline` or `clarify` case routed correctly passes without SQL.
- A non-SQL case that produces SQL fails router accuracy even if the SQL executes.

## Metrics

Primary metric:

- **Execution accuracy:** successful SQL trials divided by all expected-SQL trials.

Required secondary metrics:

- Router accuracy.
- Executable-SQL rate.
- First-attempt execution accuracy.
- Retry recovery rate.
- Average SQL-generation attempts.
- Per-case score, reported as `0/3` through `3/3`.
- Mean and percentile latency.
- Accuracy grouped by tag.
- Guardrail rejection rate once guardrails exist.

Every metric is reported per trial. Aggregate reports must not replace three trials
with a best-of-three score.

## Failure Categories

The evaluator records one primary failure category per failed trial:

- `route_mismatch`
- `missing_prediction`
- `parse_error`
- `invented_table`
- `invented_column`
- `validation_rejection`
- `execution_error`
- `timeout`
- `result_mismatch`
- `wrong_shape`
- `wrong_order`

SQLGlot and guardrail-specific categories become available in Phase 3c. Until then,
unknown SQL failures use `execution_error` with the database error stored separately.

## Runner Interface

The custom Python runner receives:

- Dataset manifest path.
- Case-set path.
- Prediction source or NLQ pipeline adapter.
- Trial count, defaulting to three.
- Query timeout.
- Output report path.

Its flow is:

```text
verify manifest and database checksum
→ validate cases and gold SQL
→ run each case for three fresh trials
→ compare routes and SQL results
→ calculate aggregate and per-tag metrics
→ print summary
→ write JSON report
```

The core runner uses standard Python JSON, hashing, timing, and SQLite facilities.
Langfuse is an output integration added later, not the source of scoring truth.

## Report Format

Each timestamped report records:

- Run ID and timestamp.
- Dataset ID and database checksum.
- Case-set version or Git commit.
- Model, prompt, and pipeline configuration when available.
- Trial count and cache policy.
- Aggregate metrics.
- Per-tag metrics.
- Per-case and per-trial predictions, results, latency, attempts, and failures.

Concrete per-case output:

```json
{
  "case_id": "stored_run_count",
  "tags": ["runs", "count", "single-table"],
  "case_level_score": {
    "passes": 3,
    "trials": 3
  },
  "trials": [
    {
      "trial": 1,
      "prediction": {
        "route": "sql",
        "sql": "SELECT COUNT(*) FROM runs"
      },
      "runtime": {
        "attempt_count": 1,
        "latency_ms": 820
      },
      "evaluation": {
        "expected_route": "sql",
        "route_correct": true,
        "gold_result": {
          "row_count": 1,
          "sha256": "<hash of normalized gold rows>",
          "preview": [[659515]]
        },
        "predicted_result": {
          "row_count": 1,
          "sha256": "<hash of normalized predicted rows>",
          "preview": [[659515]]
        },
        "trial_passed": true,
        "sql_execution_succeeded": true,
        "failure_category": null,
        "error": null
      }
    }
  ]
}
```

The evaluator compares complete normalized results in memory. Reports store row counts,
deterministic result hashes, and short previews instead of dumping arbitrarily large
result tables. Ordered comparisons hash rows in returned order; unordered comparisons
canonicalize rows before hashing. The preview limit is fixed in runner configuration
and recorded in the report.

Reports must contain enough metadata to reproduce and compare an experiment without
depending on Langfuse.

## Error Handling

- Database checksum mismatch: abort the evaluation run.
- Invalid case structure: abort before model calls begin.
- Duplicate case ID: abort before model calls begin.
- Missing or failing gold SQL: mark a dataset defect and abort the affected case.
- Missing prediction: score the trial as failed.
- Generated SQL error or timeout: score the trial as failed and continue.
- Report write failure: return a non-zero process exit status.

## Verification

Before Phase 3a is complete:

- The manifest checksum matches the frozen database.
- Every gold SQL query executes successfully.
- The evaluator handles scalar, ordered, and unordered results.
- Float tolerance behaves as specified.
- Duplicate rows are preserved during comparison.
- Broken generated SQL scores zero without stopping the full run.
- Broken gold SQL is identified as a dataset defect.
- Three trials produce three independent trial records per case.
- Response caching is disabled during evaluation.
- Aggregate, case-level, and per-tag metrics are correct on known fixtures.
- The JSON report contains dataset, case-set, and pipeline version metadata.

## Later Integration

Phase 3e connects the evaluator to the LangGraph pipeline. Phase 3g sends each trial's
trace, spans, scores, and metadata to Langfuse and groups complete runs as named
experiments. These integrations must use the deterministic evaluator's result as the
execution-accuracy source of truth.

## Design References

- [BIRD, NeurIPS 2023](https://proceedings.neurips.cc/paper_files/paper/2023/hash/83fc8fab1710363050bbd1d4b8cc0021-Abstract-Datasets_and_Benchmarks.html)
  provides question-SQL pairs and execution-based scoring across real databases.
- [BIRD Mini-Dev evaluator](https://github.com/bird-bench/mini_dev/blob/main/evaluation/evaluation_ex.py)
  executes predicted and gold SQL and compares result sets. This project preserves
  row order and duplicate counts when required instead of always converting to sets.
- [Semantic Evaluation for Text-to-SQL with Distilled Test Suites, EMNLP 2020](https://aclanthology.org/2020.emnlp-main.29/)
  motivates execution-based semantic evaluation rather than SQL-text matching.
- [Dr.Spider, ICLR 2023](https://mlanthology.org/iclr/2023/chang2023iclr-dr/)
  motivates diagnostic categories instead of reporting only aggregate accuracy.
- [Spider 2.0, ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/46c10f6c8ea5aa6f267bcdabcb123f97-Abstract-Conference.html)
  demonstrates execution-focused evaluation for modern agentic SQL workflows.
