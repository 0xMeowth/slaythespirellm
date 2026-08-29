# Phase 3a Offline Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, model-independent evaluator that versions the SQLite snapshot, loads verified StS2 cases, compares predicted-SQL results with gold-SQL results, runs three independent trials, and writes reproducible reports.

**Architecture:** Separate authored case data, prediction generation, deterministic scoring, and reporting. The evaluator calls a predictor three times per case, attaches measured runtime metadata, and passes each trial record to a scorer using read-only SQLite. The Phase 3a offline evaluation uses fake predictors; Phase 3e later supplies the real LangGraph predictor without changing scoring.

**Tech Stack:** Python 3.13+, uv, pytest, and stdlib `dataclasses`, `json`, `hashlib`, `sqlite3`, `statistics`, and `time`.

## Global Constraints

- Follow `docs/specs/phase-3a-evaluation.md`; update it if implementation changes a documented decision.
- Keep `data/spire.db` local and gitignored; commit only its manifest.
- Open evaluation databases read-only using SQLite URI mode.
- Default to three independent trials; never report best-of-three.
- Disable response, semantic, memory, and shared-state caches during trials.
- Never expose gold SQL, held-out cases, or evaluator output to the LLM.
- Use only `runs`, `run_cards`, `run_relics`, `run_card_choices`, `cards`, and `relics`.
- Do not add LangChain, LangGraph, Langfuse, SQLGlot, or DSPy to the Phase 3a offline evaluation.
- Show every proposed commit message to the user before committing.
- Never add a `Co-Authored-By` trailer.

## File Map

- `eval/models.py` — immutable case, prediction, trial, result, and report types.
- `eval/cases.py` — strict JSON case loading.
- `eval/manifest.py` — snapshot manifest creation and SHA-256 verification.
- `eval/sqlite_eval.py` — read-only SQL execution and timeout handling.
- `eval/compare.py` — scalar, ordered, unordered, and float-tolerant comparison.
- `eval/run_evaluation.py` — trial orchestration, scoring, metrics, and report writing.
- `eval/__main__.py` — manifest and case-validation CLI.
- `eval/datasets/manifest.json` — frozen database metadata.
- `eval/cases/development.json` — ten real StS2 development cases.
- `eval/cases/held_out.json` — empty until expansion toward forty cases.
- `tests/eval/` — focused unit and CLI tests using temporary SQLite databases.

---

### Task 1: Models and Case Loading

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `eval/__init__.py`
- Create: `eval/models.py`
- Create: `eval/cases.py`
- Create: `tests/eval/test_cases.py`

**Interfaces:**
- Consumes: a JSON array of eval cases.
- Produces: `EvalCase`, prediction/trial/result dataclasses, `Predictor`, and `load_cases(path: Path) -> list[EvalCase]`.

- [ ] **Step 1: Add test tooling**

Run:

```bash
uv add --dev pytest
```

Expected: pytest appears in the development dependency group and `uv.lock` updates.

- [ ] **Step 2: Write failing loader tests**

Create tests for a valid SQL case, duplicate IDs, missing `gold_sql`, invalid routes,
invalid comparison modes, negative float tolerance, and SQL fields on non-SQL cases.

```python
def test_loads_sql_case(tmp_path):
    path = write_cases(tmp_path, [{
        "id": "stored_run_count",
        "question": "How many runs are stored?",
        "expected_route": "sql",
        "gold_sql": "SELECT COUNT(*) FROM runs",
        "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
        "tags": ["runs", "count"],
    }])
    case = load_cases(path)[0]
    assert case.id == "stored_run_count"
    assert case.comparison.mode == "scalar"


def test_rejects_duplicate_ids(tmp_path):
    path = write_cases(tmp_path, [decline_case("same"), decline_case("same")])
    with pytest.raises(ValueError, match="duplicate case id"):
        load_cases(path)
```

- [ ] **Step 3: Verify the tests fail**

Run: `uv run python -m pytest tests/eval/test_cases.py -q`

Expected: import failure because `eval.cases` does not exist.

- [ ] **Step 4: Implement exact domain types**

Create these types in `eval/models.py`:

```python
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
```

Also define `ResultSummary`, `ComparisonResult`, and `ScoredTrial` with the exact fields
from the concrete per-trial output in the spec.

- [ ] **Step 5: Implement strict loading**

`load_cases()` must require a JSON array, reject duplicate IDs, reject unknown fields,
validate route-specific requirements, convert tags to tuples, and preserve case order.

```python
def load_cases(path: Path) -> list[EvalCase]:
    raw_cases = json.loads(path.read_text())
    if not isinstance(raw_cases, list):
        raise ValueError("case file must contain a JSON array")
    cases = [_parse_case(raw) for raw in raw_cases]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate case id")
    return cases
```

- [ ] **Step 6: Run tests**

Run: `uv run python -m pytest tests/eval/test_cases.py -q`

Expected: all loader tests pass.

- [ ] **Step 7: Request commit approval**

Proposed commit message: `Add evaluation case models and validation`

---

### Task 2: Frozen Snapshot Manifest

**Files:**
- Modify: `eval/models.py`
- Create: `eval/manifest.py`
- Create: `tests/eval/test_manifest.py`

**Interfaces:**
- Consumes: an SQLite path and manifest JSON.
- Produces: `DatasetManifest`, `sha256_file`, `create_manifest`, `load_manifest`, `write_manifest`, and `verify_manifest`.

- [ ] **Step 1: Write failing hash and verification tests**

```python
def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "snapshot.db"
    path.write_bytes(b"snapshot")
    assert sha256_file(path) == hashlib.sha256(b"snapshot").hexdigest()


def test_verify_rejects_changed_database(tmp_path):
    database = tmp_path / "snapshot.db"
    database.write_bytes(b"before")
    manifest = create_manifest(
        dataset_id="test-snapshot",
        database=database,
        manifest_database_path="snapshot.db",
        schema_git_commit="abc123",
        run_count=1,
        earliest_run_date="2026-06-01",
        latest_run_date="2026-07-29",
    )
    database.write_bytes(b"after")
    with pytest.raises(ValueError, match="checksum mismatch"):
        verify_manifest(manifest, tmp_path)
```

Also test relative-path enforcement, missing files, JSON round-trip, and empty `runs`.

- [ ] **Step 2: Verify the tests fail**

Run: `uv run python -m pytest tests/eval/test_manifest.py -q`

Expected: import failure because `eval.manifest` does not exist.

- [ ] **Step 3: Implement the manifest type**

```python
@dataclass(frozen=True)
class DatasetManifest:
    dataset_id: str
    database: str
    created_at: str
    earliest_run_date: str
    latest_run_date: str
    run_count: int
    database_sha256: str
    schema_git_commit: str
```

- [ ] **Step 4: Implement creation and verification**

- Hash in 8 MiB chunks so the 16 GB database is never loaded into memory.
- Store the database path relative to the repository root.
- Inspect `COUNT(*)`, `MIN(submitted_at)`, and `MAX(submitted_at)` through a read-only
  SQLite connection.
- Serialize ordered fields with two-space indentation and a trailing newline.
- Resolve the path from the supplied project root and compare the exact SHA-256.

Use:

```python
uri = f"file:{database.resolve()}?mode=ro"
with sqlite3.connect(uri, uri=True) as connection:
    row = connection.execute(
        "SELECT COUNT(*), MIN(submitted_at), MAX(submitted_at) FROM runs"
    ).fetchone()
```

- [ ] **Step 5: Run tests**

Run: `uv run python -m pytest tests/eval/test_manifest.py -q`

Expected: all manifest tests pass without reading `data/spire.db`.

- [ ] **Step 6: Request commit approval**

Proposed commit message: `Add frozen dataset manifest verification`

---

### Task 3: Read-Only Execution and Comparison

**Files:**
- Create: `eval/sqlite_eval.py`
- Create: `eval/compare.py`
- Create: `tests/eval/conftest.py`
- Create: `tests/eval/test_sqlite_eval.py`
- Create: `tests/eval/test_compare.py`

**Interfaces:**
- Produces: `execute_query(database, sql, timeout_seconds) -> QueryResult`, `compare_results(gold, predicted, spec) -> ComparisonResult`, and `summarize_result(result, mode, preview_limit) -> ResultSummary`.

- [ ] **Step 1: Create a temporary SQLite fixture**

Use this exact data:

```sql
CREATE TABLE numbers (label TEXT NOT NULL, value REAL NOT NULL);
INSERT INTO numbers VALUES ('a', 1.0), ('b', 2.0), ('b', 2.0);
```

- [ ] **Step 2: Write failing executor tests**

Test successful SELECT, invalid SQL, write rejection, and a recursive-CTE timeout.

```python
def test_executes_select_read_only(sample_database):
    result = execute_query(
        sample_database,
        "SELECT label, value FROM numbers ORDER BY label, rowid",
        timeout_seconds=1.0,
    )
    assert result.rows == (("a", 1.0), ("b", 2.0), ("b", 2.0))
```

- [ ] **Step 3: Write failing comparator tests**

Test scalar tolerance, reversed ordered rows, reversed unordered rows, duplicate-count
mismatch, wrong column count, empty results, and deterministic hashes.

```python
def test_unordered_rows_preserves_duplicates():
    gold = QueryResult(1, (("a",), ("a",)))
    predicted = QueryResult(1, (("a",),))
    result = compare_results(gold, predicted, ComparisonSpec("unordered_rows"))
    assert result.failure_category == "wrong_shape"


def test_ordered_rows_rejects_reversed_order():
    gold = QueryResult(1, (("a",), ("b",)))
    predicted = QueryResult(1, (("b",), ("a",)))
    result = compare_results(gold, predicted, ComparisonSpec("ordered_rows"))
    assert result.failure_category == "wrong_order"


def test_scalar_accepts_value_within_tolerance():
    gold = QueryResult(1, ((0.5,),))
    predicted = QueryResult(1, ((0.50001,),))
    result = compare_results(
        gold,
        predicted,
        ComparisonSpec("scalar", float_tolerance=0.0001),
    )
    assert result.passed
```

- [ ] **Step 4: Verify the tests fail**

Run: `uv run python -m pytest tests/eval/test_sqlite_eval.py tests/eval/test_compare.py -q`

Expected: missing-module failures.

- [ ] **Step 5: Implement timeout-aware read-only execution**

```python
def execute_query(database: Path, sql: str, timeout_seconds: float) -> QueryResult:
    deadline = time.monotonic() + timeout_seconds
    uri = f"file:{database.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.set_progress_handler(
            lambda: int(time.monotonic() >= deadline), 1_000
        )
        cursor = connection.execute(sql)
        return QueryResult(
            column_count=len(cursor.description or ()),
            rows=tuple(tuple(row) for row in cursor.fetchall()),
        )
```

Wrap SQLite errors in `QueryExecutionError(category, message)`. Use `timeout` when the
message is `interrupted`; otherwise use `execution_error`. Read-only mode rejects writes.

- [ ] **Step 6: Implement exact comparison semantics**

- `scalar`: exactly one row and one column.
- `ordered_rows`: equal shape, duplicate counts, values, and order.
- `unordered_rows`: equal shape and greedy one-to-one row matching so duplicates and
  float tolerance both work.
- Floats: `math.isclose(rel_tol=0.0, abs_tol=spec.float_tolerance)`.
- Strings, bytes, integers, and `None`: exact type and value equality.
- Aliases: ignored because only column count and values are stored.
- Summaries: full normalized result hashed; only `preview_limit` rows retained.
- Ordered hash: returned order. Unordered hash: canonical encoded-row order.

- [ ] **Step 7: Run tests**

Run: `uv run python -m pytest tests/eval/test_sqlite_eval.py tests/eval/test_compare.py -q`

Expected: all executor and comparator tests pass.

- [ ] **Step 8: Request commit approval**

Proposed commit message: `Add deterministic SQL result scoring`

---

### Task 4: Three-Trial Runner and Reports

**Files:**
- Modify: `eval/models.py`
- Create: `eval/run_evaluation.py`
- Create: `tests/eval/test_run_evaluation.py`

**Interfaces:**
- Produces: `collect_trial_records`, `score_trial`, `run_evaluation`, `EvaluationReport`, and `write_report`.

- [ ] **Step 1: Write a fake sequence predictor**

```python
class SequencePredictor:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.questions = []

    def predict(self, question):
        self.questions.append(question)
        return next(self.outcomes)
```

- [ ] **Step 2: Write failing trial tests**

Assert one case calls the predictor three times by default, numbers trials `1..3`,
attaches the case ID, obtains attempt count from `AgentRunResult`, measures latency
with an injected clock, preserves identical predictions as separate records, and allows
`trial_count=1` for fast development.

- [ ] **Step 3: Write failing scoring tests**

Cover equivalent SQL, execution error continuation, route mismatch, correct decline,
correct clarification, broken gold SQL as `GoldQueryError`, and retry success with
`attempt_count=2`.

- [ ] **Step 4: Write failing metric tests**

Assert these exact formulas:

- Execution accuracy: passed SQL trials divided by expected-SQL trials.
- Router accuracy: route-correct trials divided by all trials.
- Executable-SQL rate: executable predictions divided by expected-SQL trials.
- First-attempt accuracy: passing SQL trials with `attempt_count == 1` divided by all
  expected-SQL trials.
- Retry recovery: passing retried trials divided by retried trials; `null` if none.
- Average attempt count: arithmetic mean across all trials.
- Case-level score: pass count over trial count per case.
- Tags: case count, trial count, and pass rate.
- Latency: mean, p50, and nearest-rank p95.

- [ ] **Step 5: Verify the tests fail**

Run: `uv run python -m pytest tests/eval/test_run_evaluation.py -q`

Expected: missing runner and report types.

- [ ] **Step 6: Implement trial collection**

```python
def collect_trial_records(case, predictor, trial_count=3, clock=time.perf_counter):
    records = []
    for trial in range(1, trial_count + 1):
        started = clock()
        agent_result = predictor.predict(case.question)
        elapsed_ms = (clock() - started) * 1000
        records.append(TrialRecord(
            case_id=case.id,
            trial=trial,
            prediction=AgentPrediction(
                route=agent_result.route,
                sql=agent_result.generated_sql,
            ),
            runtime=RuntimeMetadata(agent_result.attempt_count, elapsed_ms),
        ))
    return records
```

Reject `trial_count < 1` and attempt counts outside `1..max_attempts`. The future
LangGraph adapter creates fresh graph state inside every `predict()` call.

- [ ] **Step 7: Implement deterministic scoring**

For each trial: compare route, skip SQL for correctly routed non-SQL cases, execute gold
SQL, execute predicted SQL, compare complete results, attach summaries, and preserve
runtime metadata. A gold failure raises `GoldQueryError`; a predicted failure scores
zero and does not stop later trials.

- [ ] **Step 8: Implement report serialization**

Use the exact top-level fields from the spec: run ID, timestamp, dataset metadata,
case-set hash, configuration, aggregate metrics, per-tag metrics, and cases containing
all trial records and scored outputs. Write atomically through a sibling `.tmp` file;
never serialize unsupported objects with `default=str`.

- [ ] **Step 9: Run tests**

Run: `uv run python -m pytest tests/eval/test_run_evaluation.py -q`

Expected: all orchestration, scoring, continuation, metric, and serialization tests pass.

- [ ] **Step 10: Request commit approval**

Proposed commit message: `Add three-trial evaluation runner and reports`

---

### Task 5: CLI, Compact Snapshot, Manifest, and Initial Dataset

**Files:**
- Create: `eval/__main__.py`
- Create: `eval/snapshot.py`
- Create: `tests/eval/test_cli.py`
- Create: `tests/eval/test_snapshot.py`
- Create: `eval/datasets/manifest.json`
- Create: `eval/cases/development.json`
- Create: `eval/cases/held_out.json`
- Modify when needed: `docs/specs/phase-3a-evaluation.md`

**Interfaces:**
- Produces: `snapshot create`, `manifest create`, `manifest verify`, and
  `cases validate` CLI commands.

- [ ] **Step 1: Write failing CLI tests**

Use `subprocess.run()` against temporary files. The manifest-verification test calls:

```python
completed = subprocess.run(
    [
        sys.executable,
        "-m",
        "eval",
        "manifest",
        "verify",
        "--manifest",
        str(manifest_path),
    ],
    capture_output=True,
    text=True,
)
assert completed.returncode == 0
```

Also test successful manifest creation and non-zero exits for checksum mismatch and
invalid gold SQL. Hide tracebacks unless `--debug` is supplied.

- [ ] **Step 2: Verify the tests fail**

Run: `uv run python -m pytest tests/eval/test_cli.py -q`

Expected: failure because `eval.__main__` does not exist.

- [ ] **Step 3: Implement argparse commands**

- `manifest create`: inspect run coverage, hash the database, and write the manifest.
- `manifest verify`: verify the manifest and database checksum.
- `cases validate`: verify the snapshot, load cases, execute all gold SQL read-only,
  and print SQL-case and router-case counts plus short previews.

Do not add an LLM command. Phase 3e adds the live LangGraph adapter.

- [ ] **Step 4: Author ten development cases**

Write this JSON array to `eval/cases/development.json`:

```json
[
  {
    "id": "stored_run_count",
    "question": "How many runs are stored?",
    "expected_route": "sql",
    "gold_sql": "SELECT COUNT(*) FROM runs",
    "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
    "tags": ["runs", "count", "single-table"]
  },
  {
    "id": "non_abandoned_run_count",
    "question": "How many stored runs were not abandoned?",
    "expected_route": "sql",
    "gold_sql": "SELECT COUNT(*) FROM runs WHERE was_abandoned = 0",
    "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
    "tags": ["runs", "count", "filter"]
  },
  {
    "id": "official_character_run_counts",
    "question": "How many runs are recorded for each official character?",
    "expected_route": "sql",
    "gold_sql": "SELECT character, COUNT(*) AS run_count FROM runs WHERE character IN ('IRONCLAD', 'SILENT', 'DEFECT', 'NECROBINDER', 'REGENT') GROUP BY character",
    "comparison": {"mode": "unordered_rows", "float_tolerance": 0.0001},
    "tags": ["runs", "group-by", "filter"]
  },
  {
    "id": "silent_ascension_10_win_rate",
    "question": "What is Silent's win rate at Ascension 10, excluding abandoned runs?",
    "expected_route": "sql",
    "gold_sql": "SELECT AVG(CAST(win AS REAL)) FROM runs WHERE character = 'SILENT' AND ascension = 10 AND was_abandoned = 0",
    "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
    "tags": ["runs", "win-rate", "aggregation", "filter"]
  },
  {
    "id": "official_character_win_rate_ranking",
    "question": "Rank the five official characters by non-abandoned win rate.",
    "expected_route": "sql",
    "gold_sql": "SELECT character, AVG(CAST(win AS REAL)) AS win_rate FROM runs WHERE was_abandoned = 0 AND character IN ('IRONCLAD', 'SILENT', 'DEFECT', 'NECROBINDER', 'REGENT') GROUP BY character ORDER BY win_rate DESC, character ASC",
    "comparison": {"mode": "ordered_rows", "float_tolerance": 0.0001},
    "tags": ["runs", "win-rate", "group-by", "ranking"]
  },
  {
    "id": "silent_gold_axe_final_decks",
    "question": "How many Silent final decks contain Gold Axe?",
    "expected_route": "sql",
    "gold_sql": "SELECT COUNT(DISTINCT rc.run_id) FROM run_cards AS rc JOIN runs AS r ON r.run_id = rc.run_id WHERE rc.card_id = 'GOLD_AXE' AND r.character = 'SILENT'",
    "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
    "tags": ["runs", "cards", "joins", "distinct", "entity"]
  },
  {
    "id": "ironclad_winning_fishing_rod_runs",
    "question": "How many winning Ironclad runs contain Fishing Rod?",
    "expected_route": "sql",
    "gold_sql": "SELECT COUNT(DISTINCT rr.run_id) FROM run_relics AS rr JOIN runs AS r ON r.run_id = rr.run_id WHERE rr.relic_id = 'FISHING_ROD' AND r.character = 'IRONCLAD' AND r.win = 1",
    "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
    "tags": ["runs", "relics", "joins", "distinct", "entity", "filter"]
  },
  {
    "id": "gold_axe_offer_count",
    "question": "How many times was Gold Axe offered?",
    "expected_route": "sql",
    "gold_sql": "SELECT COUNT(*) FROM run_card_choices WHERE card_id = 'GOLD_AXE'",
    "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
    "tags": ["card-choices", "entity", "count", "filter"]
  },
  {
    "id": "silent_strategy_advice",
    "question": "How should I play the Silent?",
    "expected_route": "decline",
    "tags": ["router", "strategy"]
  },
  {
    "id": "ambiguous_strike_win_rate",
    "question": "What is Strike's win rate?",
    "expected_route": "clarify",
    "tags": ["router", "ambiguity", "entity"]
  }
]
```

Write `[]` plus a trailing newline to `eval/cases/held_out.json`. Create the 24/16 split
only after expansion to forty cases.

- [ ] **Step 5: Create the compact snapshot and frozen manifest**

Run:

```bash
uv run python -m eval snapshot create \
  --source data/spire.db \
  --output data/spire_eval_2026-07-29.db

uv run python -m eval manifest create \
  --database data/spire_eval_2026-07-29.db \
  --dataset-id sts2-2026-07-29 \
  --schema-git-commit "$(git log -1 --format=%H -- schema.sql)" \
  --output eval/datasets/manifest.json
```

Expected metadata:

```text
run_count: 659515
earliest: 2026-06-01T03:19:17.754000
latest: 2026-07-29T17:08:27.136000
```

- [ ] **Step 6: Validate real gold SQL**

Run:

```bash
uv run python -m eval manifest verify --manifest eval/datasets/manifest.json
uv run python -m eval cases validate \
  --cases eval/cases/development.json \
  --manifest eval/datasets/manifest.json
```

Expected: checksum matches; eight SQL cases and two router cases validate. Manually
review result previews so executable but semantically wrong gold SQL does not pass.

- [ ] **Step 7: Reconcile the spec**

Compare implemented field names, report structure, trial behavior, and cache policy
with `docs/specs/phase-3a-evaluation.md`. Update the spec before committing if any
decision changed; explain every change in the handoff.

- [ ] **Step 8: Run CLI tests**

Run: `uv run python -m pytest tests/eval/test_cli.py -q`

Expected: all CLI tests pass.

- [ ] **Step 9: Request commit approval**

Proposed commit message: `Add initial StS2 evaluation dataset and CLI`

---

### Task 6: Full Verification and Handoff

**Files:**
- Modify: `ROADMAP.md`
- Modify only if verified behavior differs: `docs/specs/phase-3a-evaluation.md`

- [ ] **Step 1: Run all tests**

Run: `uv run python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 2: Verify imports**

Run:

```bash
uv run python -c "import eval; from eval.run_evaluation import run_evaluation"
```

Expected: exit zero without output.

- [ ] **Step 3: Re-verify snapshot and gold SQL**

Run:

```bash
uv run python -m eval manifest verify --manifest eval/datasets/manifest.json
uv run python -m eval cases validate --cases eval/cases/development.json --manifest eval/datasets/manifest.json
```

Expected: checksum and all eight gold SQL cases pass.

- [ ] **Step 4: Verify three-trial behavior**

Run:

```bash
uv run python -m pytest tests/eval/test_run_evaluation.py -q -k "three_trials or report"
```

Expected: one case produces three independent trial records, three scored outputs, and
case-level score `3/3`; no response or semantic cache is enabled.

- [ ] **Step 5: Update roadmap status**

Mark `3a Offline evaluation` done only after Steps 1–4 pass. Leave `3e End-to-end baseline
eval` pending because the real model and LangGraph pipeline do not exist. Note that ten
development cases exist and the held-out split awaits expansion toward forty.

- [ ] **Step 6: Inspect the final diff**

Run:

```bash
git status --short
git diff --check
git diff --stat
```

Expected: no database, secret, cache, or unrelated file is staged.

- [ ] **Step 7: Request final documentation commit approval if needed**

Proposed message: `Complete Phase 3a offline evaluation`

Do not create an empty commit when Task 6 changes no files. Stop after Phase 3a and
review Phase 3b before implementation.
