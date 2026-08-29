# Phase 3d LangGraph Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build an explicit LangGraph `StateGraph` that routes questions, generates SQL with a configurable model, validates and executes through Phase 3c, retries correctable failures up to three SQL-generation attempts, and can be inspected in LangGraph Studio.

**Architecture:** Keep graph state JSON-compatible and inject the model, schema context, database path, and execution limits when building the graph. The model only returns route decisions and SQL text; deterministic nodes parse, validate, execute, and control retries. Automated tests use scripted fake models, while one final Studio walkthrough uses the configured real model. SEA-LION is the initial baseline provider.

**Tech Stack:** Python 3.13+, uv, LangChain `BaseChatModel`, LangGraph Graph API, LangGraph CLI in-memory server, SQLGlot, SQLite, and pytest.

**Spec:** `docs/specs/phase-3d-langgraph-pipeline.md`

## Global Constraints

- Use LangGraph's Graph API and explicit `StateGraph`; do not use the Functional API or `create_agent`.
- The model never receives a callable SQLite tool or database connection.
- Only `nlq.sql_policy.validate_sql()` and `nlq.sql_executor.guard_and_execute_sql()` enforce and execute generated SQL.
- Use the verified, cached Phase 3b `SchemaContext`; never render hidden tables into prompts.
- Keep graph state JSON-compatible so LangGraph Studio can display it.
- Count no more than three SQL-generation attempts; LangChain transport retries do not increment this counter.
- Treat malformed model output, validation rejection, and SQLite execution failure as correctable generation failures.
- Treat router-output failure and exhausted model transport failure as terminal pipeline failures.
- Keep answer synthesis, Phase 3a evaluator integration, Langfuse, entity linking, and few-shot retrieval out of Phase 3d.
- Automated tests must not use the network, API keys, or the frozen production-size database.
- The manual Studio walkthrough must use the configured real model with `LANGSMITH_TRACING=false` and `LANGGRAPH_CLI_NO_ANALYTICS=1`.
- Show every proposed commit message before committing and never add a `Co-Authored-By` trailer.
- Stop after each task for user code review before starting the next task.

---

## File Map

Create:

```text
nlq/text_to_sql_state.py
nlq/text_to_sql_model_output.py
nlq/text_to_sql_graph.py
nlq/studio_graph.py
langgraph.json
tests/nlq/test_text_to_sql_state.py
tests/nlq/test_text_to_sql_model_output.py
tests/nlq/test_text_to_sql_graph.py
tests/nlq/test_studio_graph.py
```

Modify:

```text
pyproject.toml
uv.lock
ROADMAP.md
docs/specs/phase-3d-langgraph-pipeline.md only if implementation changes a documented decision
```

Responsibilities:

- `text_to_sql_state.py`: graph-state schema, initial-state construction, immutable public result.
- `text_to_sql_model_output.py`: strict router JSON and generated-SQL response parsing.
- `text_to_sql_graph.py`: prompts, named nodes, conditional edges, graph construction, and invocation wrapper.
- `studio_graph.py`: environment-backed dependency construction for the local Agent Server.
- `langgraph.json`: maps Studio to the graph factory and gitignored `.env` file.

---

### Task 1: Typed Graph State and Public Result

**Files:**
- Create: `nlq/text_to_sql_state.py`
- Create: `tests/nlq/test_text_to_sql_state.py`

**Interfaces:**
- Produces: `TextToSqlState`, `TextToSqlRunResult`, `create_initial_state(question)`, and `create_run_result(state)`.
- Consumed by: Tasks 3–5.

- [ ] **Step 1: Write failing initial-state tests**

Create `tests/nlq/test_text_to_sql_state.py` with tests equivalent to:

```python
import pytest

from nlq.text_to_sql_state import (
    create_initial_state,
    create_run_result,
)


def test_creates_json_compatible_initial_state():
    state = create_initial_state("How many runs are stored?")

    assert state == {
        "question": "How many runs are stored?",
        "route": None,
        "route_reason": None,
        "generated_sql": None,
        "attempt_count": 0,
        "error_category": None,
        "error_message": None,
        "columns": [],
        "rows": [],
        "result_truncated": False,
        "status": "running",
    }


@pytest.mark.parametrize("question", ["", "   "])
def test_rejects_blank_question(question):
    with pytest.raises(ValueError, match="question must be non-blank"):
        create_initial_state(question)


def test_converts_final_state_to_immutable_result():
    state = create_initial_state("How many runs are stored?")
    state.update(
        route="sql",
        generated_sql="SELECT COUNT(*) FROM runs",
        attempt_count=1,
        columns=["COUNT(*)"],
        rows=[[12]],
        status="succeeded",
    )

    result = create_run_result(state)

    assert result.route == "sql"
    assert result.generated_sql == "SELECT COUNT(*) FROM runs"
    assert result.attempt_count == 1
    assert result.columns == ("COUNT(*)",)
    assert result.rows == ((12,),)
    assert result.status == "succeeded"
```

- [ ] **Step 2: Run the focused tests and confirm the expected import failure**

Run:

```bash
uv run pytest tests/nlq/test_text_to_sql_state.py -q
```

Expected: collection fails because `nlq.text_to_sql_state` does not exist.

- [ ] **Step 3: Implement the state and result types**

Create `nlq/text_to_sql_state.py` with these public shapes:

```python
from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict


JsonScalar: TypeAlias = str | int | float | bool | None
Route: TypeAlias = Literal["sql", "decline"]
RunStatus: TypeAlias = Literal["running", "succeeded", "declined", "failed"]


class TextToSqlState(TypedDict):
    question: str
    route: Route | None
    route_reason: str | None
    generated_sql: str | None
    attempt_count: int
    error_category: str | None
    error_message: str | None
    columns: list[str]
    rows: list[list[JsonScalar]]
    result_truncated: bool
    status: RunStatus


@dataclass(frozen=True)
class TextToSqlRunResult:
    route: Route | None
    route_reason: str | None
    generated_sql: str | None
    attempt_count: int
    error_category: str | None
    error_message: str | None
    columns: tuple[str, ...]
    rows: tuple[tuple[JsonScalar, ...], ...]
    result_truncated: bool
    status: RunStatus
```

Implement `create_initial_state()` with a non-blank question check and the exact values
asserted above. Implement `create_run_result()` as a defensive conversion from mutable
state lists to immutable tuples.

- [ ] **Step 4: Run focused and adjacent tests**

Run:

```bash
uv run pytest tests/nlq/test_text_to_sql_state.py tests/nlq/test_sql_executor.py -q
```

Expected: all tests pass.

- [ ] **Step 5: Review checkpoint and commit**

Show the user the new state file and tests. Explain that state is the shared snapshot
Studio displays after each node, while `TextToSqlRunResult` is the stable object consumed
outside LangGraph.

Proposed commit:

```text
Add typed text-to-SQL graph state
```

Stop for approval before committing and before Task 2.

---

### Task 2: Deterministic Model-Output Parsers

**Files:**
- Create: `nlq/text_to_sql_model_output.py`
- Create: `tests/nlq/test_text_to_sql_model_output.py`

**Interfaces:**
- Produces: `RouteDecision`, `ModelOutputError`, `parse_route_response(text)`, and `parse_generated_sql(text)`.
- Consumed by: Task 3 graph nodes.

- [ ] **Step 1: Write failing router-parser tests**

Cover:

```python
def test_parses_sql_route():
    decision = parse_route_response(
        '{"route":"sql","reason":"answerable from run data"}'
    )
    assert decision.route == "sql"
    assert decision.reason == "answerable from run data"


def test_parses_decline_route():
    decision = parse_route_response(
        '{"route":"decline","reason":"strategy advice"}'
    )
    assert decision.route == "decline"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{}',
        '{"route":"maybe","reason":"unknown"}',
        '{"route":"sql","reason":" "}',
        '{"route":"sql","reason":"ok","extra":true}',
    ],
)
def test_rejects_malformed_route_response(text):
    with pytest.raises(ModelOutputError) as raised:
        parse_route_response(text)
    assert raised.value.category == "router_output_error"
```

- [ ] **Step 2: Write failing SQL-parser tests**

Cover:

```python
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SELECT COUNT(*) FROM runs", "SELECT COUNT(*) FROM runs"),
        (
            "```sql\nSELECT COUNT(*) FROM runs\n```",
            "SELECT COUNT(*) FROM runs",
        ),
        ("WITH x AS (SELECT 1) SELECT * FROM x", "WITH x AS (SELECT 1) SELECT * FROM x"),
    ],
)
def test_parses_generated_sql(text, expected):
    assert parse_generated_sql(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Here is the SQL: SELECT COUNT(*) FROM runs",
        "```python\nprint('x')\n```",
        "```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```",
        "DELETE FROM runs",
    ],
)
def test_rejects_non_sql_model_output(text):
    with pytest.raises(ModelOutputError) as raised:
        parse_generated_sql(text)
    assert raised.value.category == "model_output_error"
```

- [ ] **Step 3: Run tests and confirm the expected import failure**

Run:

```bash
uv run pytest tests/nlq/test_text_to_sql_model_output.py -q
```

Expected: collection fails because the parser module does not exist.

- [ ] **Step 4: Implement exact parsing rules**

Create:

```python
@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str


class ModelOutputError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category
```

`parse_route_response()` must:

1. Parse with `json.loads()`.
2. Require a JSON object with exactly `route` and `reason`.
3. Require route `sql` or `decline`.
4. Require a non-blank string reason.
5. Convert every failure into a concise `ModelOutputError("router_output_error", ...)`
   without including the full model response.

`parse_generated_sql()` must:

1. Strip outer whitespace.
2. Accept plain text beginning with `SELECT` or `WITH`, case-insensitively.
3. Accept exactly one surrounding lower- or upper-case `sql` Markdown fence.
4. Reject empty text, non-SQL fences, unmatched/multiple fences, and prose prefixes.
5. Return the inner SQL string without executing or semantically validating it.

- [ ] **Step 5: Run focused tests**

Run:

```bash
uv run pytest tests/nlq/test_text_to_sql_model_output.py -q
```

Expected: all tests pass.

- [ ] **Step 6: Review checkpoint and commit**

Show the user both parsers and explain that they convert unpredictable LLM text into
narrow Python values before LangGraph or SQLite trusts it.

Proposed commit:

```text
Parse text-to-SQL model responses
```

Stop for approval before committing and before Task 3.

---

### Task 3: First Complete StateGraph Paths

**Files:**
- Create: `nlq/text_to_sql_graph.py`
- Create: `tests/nlq/test_text_to_sql_graph.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: Phase 3b `BaseChatModel` and `SchemaContext`; Phase 3c `validate_sql()`, `guard_and_execute_sql()`, and `ExecutionLimits`; Tasks 1–2 state and parser types.
- Produces: `build_text_to_sql_graph(...)` and `run_text_to_sql_question(graph, question)`.
- Task 4 extends these interfaces with the correction loop without changing callers.

- [ ] **Step 1: Add the LangGraph runtime dependency**

Run:

```bash
uv add langgraph
```

Expected: `pyproject.toml` and `uv.lock` add LangGraph without changing existing direct
dependencies.

- [ ] **Step 2: Write a scripted fake model in the graph test**

Add a small test-only fake:

```python
from langchain_core.messages import AIMessage


class ScriptedChatModel:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def invoke(self, messages):
        self.requests.append(messages)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return AIMessage(content=response)
```

The production builder remains typed against `BaseChatModel`; the fake deliberately
implements only the `invoke()` behavior the graph consumes.

- [ ] **Step 3: Write failing first-attempt success and decline tests**

Insert one completed run into `analytical_database`, then cover:

```python
def test_runs_sql_route_to_first_attempt_success(analytical_database, schema_context):
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            "SELECT COUNT(*) AS run_count FROM runs WHERE was_abandoned = 0",
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
    )

    result = run_text_to_sql_question(graph, "How many completed runs are stored?")

    assert result.status == "succeeded"
    assert result.attempt_count == 1
    assert result.columns == ("run_count",)
    assert result.rows == ((1,),)
    assert len(model.requests) == 2


def test_declined_route_never_generates_or_executes_sql(
    analytical_database, schema_context
):
    model = ScriptedChatModel(
        ['{"route":"decline","reason":"strategy advice"}']
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
    )

    result = run_text_to_sql_question(graph, "How should I build Silent?")

    assert result.status == "declined"
    assert result.route == "decline"
    assert result.attempt_count == 0
    assert len(model.requests) == 1
```

Add a local `schema_context` fixture by rendering the existing test schema and reviewed
dictionary with the existing `manifest` fixture. Do not use the frozen database.

- [ ] **Step 4: Write failing prompt and topology tests**

Assert:

- Router request contains the question but not schema text.
- Generator request contains the question and `schema_context.text`.
- Hidden table names `raw_runs`, `sync_state`, and `sync_log` are absent.
- The compiled graph contains nodes named `route_question`, `generate_sql`,
  `validate_sql`, `execute_sql`, and `retry_or_finish`.
- A malformed router response ends with `router_output_error` and only one model call.

- [ ] **Step 5: Run focused tests and confirm failures**

Run:

```bash
uv run pytest tests/nlq/test_text_to_sql_graph.py -q
```

Expected: collection fails because `nlq.text_to_sql_graph` does not exist.

- [ ] **Step 6: Implement prompts and dependency-injected nodes**

Use fixed system prompts with these required meanings:

```text
ROUTER_SYSTEM_PROMPT
- Classify only whether the question is answerable from the statistical SQLite schema.
- Return exactly JSON with route and reason.
- Use route sql for database facts/aggregates.
- Use route decline for strategy, advice, opinion, prediction, off-topic, or unavailable data.

SQL_SYSTEM_PROMPT
- Return exactly one read-only SQLite SELECT query and no explanation.
- Use only the supplied schema.
- Exclude was_abandoned = 1 for normal run/win-rate analysis unless explicitly requested.
- Never invent tables, columns, functions, or values.
```

Create node closures inside `build_text_to_sql_graph()` so the model, schema context,
database, and limits never enter graph state.

Implement the full named topology. In this task, validation, execution, or model-output
failure may pass through `retry_or_finish`, but `retry_or_finish` ends immediately with
`status="failed"`. Task 4 enables its backward edge after the first-attempt behavior is
reviewed.

On successful execution, convert SQLite tuples to JSON-compatible lists in state.
`run_text_to_sql_question()` must create initial state, call `graph.invoke()`, and convert
the final state through `create_run_result()`.

- [ ] **Step 7: Run focused and regression tests**

Run:

```bash
uv run pytest \
  tests/nlq/test_text_to_sql_graph.py \
  tests/nlq/test_schema_context.py \
  tests/nlq/test_sql_policy.py \
  tests/nlq/test_sql_executor.py -q
```

Expected: all tests pass.

- [ ] **Step 8: Review checkpoint and commit**

Walk the user through the graph builder node by node and show the topology test. Explain
that this commit provides a complete first-attempt pipeline before introducing the
backward retry edge.

Proposed commit:

```text
Add first-pass LangGraph SQL pipeline
```

Stop for approval before committing and before Task 4.

---

### Task 4: SQL Correction and Three-Attempt Loop

**Files:**
- Create: `config.toml`
- Create: `nlq/pipeline_settings.py`
- Create: `tests/nlq/test_pipeline_settings.py`
- Modify: `nlq/text_to_sql_graph.py`
- Modify: `tests/nlq/test_text_to_sql_graph.py`
- Modify: `docs/specs/phase-3d-langgraph-pipeline.md`

**Interfaces:**
- Preserves: `build_text_to_sql_graph(...)` and `run_text_to_sql_question(...)`.
- Adds: required `max_attempts` behavior, tracked TOML configuration, and
  correction-prompt construction.

- [ ] **Step 1: Write failing validation-retry test**

Script:

```text
route: sql
attempt 1: SELECT COUNT(*) FROM raw_runs
attempt 2: SELECT COUNT(*) FROM runs
```

Assert the final result succeeds on attempt 2 and the second generator request contains:

```text
Previous SQL: SELECT COUNT(*) FROM raw_runs
Error category: unapproved_table
Error message: unapproved table: raw_runs
```

Assert it does not contain a traceback, API key, or hidden schema description.

- [ ] **Step 2: Write failing execution-retry test**

Script an allowed-table query with a nonexistent column, followed by a valid query:

```text
SELECT missing_column FROM runs
SELECT COUNT(*) FROM runs
```

Assert the first query reaches SQLite, produces `execution_error`, and the second query
succeeds with `attempt_count == 2`.

- [ ] **Step 3: Write failing exhaustion and terminal-error tests**

Cover:

- Three validation failures end with `status="failed"`, the third SQL, and
  `attempt_count == 3`.
- Malformed generated output consumes an attempt and can be corrected on the next call.
- A model invocation exception after LangChain's own retries ends with
  `model_request_error` and does not perform another graph-level model request.
- `max_attempts=0` raises `ValueError("max_attempts must be positive")` while building.
- A first-attempt success still performs exactly one SQL-generation call.

- [ ] **Step 4: Run tests and confirm retry failures**

Run:

```bash
uv run pytest tests/nlq/test_text_to_sql_graph.py -q -k "retry or attempts or model_request"
```

Expected: new retry tests fail because `retry_or_finish` still ends immediately.

- [ ] **Step 5: Implement correction prompting and backward edge**

Change `retry_or_finish` to:

```text
attempt_count < max_attempts -> status remains running -> generate_sql
attempt_count == max_attempts -> status becomes failed -> END
```

Before a correction call, `generate_sql` must read the previous SQL, error category, and
safe error message from state and append a correction section to the generator message.
After a parseable new SQL string is received, clear the old error before validation.

Increment `attempt_count` exactly once immediately before each SQL-generation model
request. Do not increment for routing, SQLGlot validation, SQLite execution, edge
routing, or LangChain's internal HTTP retries.

Catch exhausted model invocation exceptions at the generator node boundary and return:

```text
status=failed
error_category=model_request_error
error_message=model request failed
```

Do not put provider exception text into graph state.

Create tracked `config.toml` with `[nlq] max_attempts = 3` and a frozen
`PipelineSettings` loaded through `tomllib`. Reject missing, boolean, non-integer, and
non-positive values. Keep `max_attempts` required in `build_text_to_sql_graph()` so
callers must pass the validated setting.

- [ ] **Step 6: Run all graph and guardrail tests**

Run:

```bash
uv run pytest \
  tests/nlq/test_text_to_sql_graph.py \
  tests/nlq/test_sql_policy.py \
  tests/nlq/test_sql_executor.py -q
```

Expected: all tests pass.

- [ ] **Step 7: Review checkpoint and commit**

Show the user one successful correction state sequence and one exhausted sequence.
Explain the distinction between SQL-generation attempts and HTTP transport retries.

Proposed commit:

```text
Add LangGraph SQL correction loop
```

Stop for approval before committing and before Task 5.

---

### Task 5: LangGraph Studio Development Setup

**Files:**
- Create: `nlq/studio_graph.py`
- Create: `langgraph.json`
- Create: `tests/nlq/test_studio_graph.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`

**Interfaces:**
- Consumes: existing `ModelSettings`, `PipelineSettings`, `build_chat_model()`, manifest
  verification, `build_verified_schema_context()`, and Task 3 graph builder.
- Produces: `create_studio_graph()` referenced by `langgraph.json`.

- [ ] **Step 1: Add the local Agent Server dependency**

Run:

```bash
uv add --dev "langgraph-cli[inmem]"
```

Expected: the CLI appears only in the development dependency group.

- [ ] **Step 2: Write failing Studio-factory tests**

Test `create_studio_graph()` with monkeypatched dependency builders so it performs no
network call and does not require the frozen database. Assert it:

- Loads `ModelSettings` from the environment.
- Loads `PipelineSettings` from root `config.toml`.
- Calls `build_chat_model()` exactly once.
- Builds verified schema context from `eval/datasets/manifest.json` and
  `nlq/schema_dictionary.json` under the supplied project root.
- Resolves the manifest database through `verify_manifest()`.
- Passes the model, schema context, database path, and
  `PipelineSettings.max_attempts` into `build_text_to_sql_graph()`.

Keep public `create_studio_graph()` argument-free so the Agent Server can call it
without dependency injection. Put path-dependent construction in an internal
`_create_studio_graph(project_root: Path)` helper and test that helper with a temporary
root instead of hardcoding the developer's machine path.

- [ ] **Step 3: Write failing configuration-file test**

Load `langgraph.json` with `json.loads()` and assert:

```python
assert config["dependencies"] == ["."]
assert config["graphs"] == {
    "sts2_text_to_sql": "./nlq/studio_graph.py:create_studio_graph"
}
assert config["env"] == ".env"
```

- [ ] **Step 4: Run focused tests and confirm failures**

Run:

```bash
uv run pytest tests/nlq/test_studio_graph.py -q
```

Expected: collection fails because the Studio module and configuration do not exist.

- [ ] **Step 5: Implement the Studio graph factory and config**

`create_studio_graph()` must:

1. Load and validate `ModelSettings`.
2. Load and validate root `config.toml` as `PipelineSettings`.
3. Build the LangChain model without invoking it.
4. Build verified Phase 3b schema context.
5. Load and verify the frozen manifest to resolve the database path.
6. Return `build_text_to_sql_graph(...)` with explicit `max_attempts`.

Do not create a model request at module import. The `langgraph.json` target is the
factory function, which the local Agent Server calls when it needs the graph.

Create the exact `langgraph.json` shape asserted in Step 3, including its schema URL.

- [ ] **Step 6: Run automated Studio and full tests**

Run:

```bash
uv run pytest tests/nlq/test_studio_graph.py -q
uv run pytest -q
```

Expected: all tests pass without network access.

- [ ] **Step 7: Review checkpoint and commit**

Show the user `langgraph.json`, the factory, and how Studio discovers the graph. Do not
start the real-model walkthrough until the user approves the setup.

Proposed commit:

```text
Add LangGraph Studio development setup
```

Stop for approval before committing and before Task 6.

---

### Task 6: Real-Model Studio Walkthrough and Phase Completion

**Files:**
- Modify: `ROADMAP.md`
- Modify only if observed behavior changes a decision: `docs/specs/phase-3d-langgraph-pipeline.md`

**Interfaces:**
- Verifies the complete Phase 3d graph through the local Agent Server.
- Produces no test fixture, cached response, trace export, or checked-in model output.

- [ ] **Step 1: Prepare local-only worktree links if needed**

When implementation is in `.worktrees/phase-3d-langgraph-pipeline`, create untracked,
gitignored links only if `.env` or `data` are absent:

```bash
ln -s ../../.env .env
ln -s ../../data data
```

Confirm with `git status --short` that neither link is staged or tracked. If working
directly in the main checkout, skip this step.

- [ ] **Step 2: Start the local Agent Server with tracing disabled**

Run:

```bash
LANGSMITH_TRACING=false \
LANGGRAPH_CLI_NO_ANALYTICS=1 \
uv run langgraph dev
```

Expected: the command prints a local API URL and LangGraph Studio URL without exposing
the model-provider API key.

- [ ] **Step 3: Complete the required visual walkthrough**

Open Studio and select `sts2_text_to_sql`.

First submit:

```text
How many completed non-abandoned runs are stored?
```

Verify visually:

- DAG nodes and conditional edges render.
- The visited path is route → generate → validate → execute.
- The route is `sql`.
- The generated SQL is visible in graph state.
- `attempt_count` is at least 1 and no greater than 3.
- Validation succeeds and result rows are visible.

Then submit:

```text
How should I build Silent?
```

Verify visually that the decline edge reaches `END`, `attempt_count == 0`, and no SQL
or result rows are produced.

- [ ] **Step 4: Stop the development server and verify no tracked artifacts appeared**

Stop `langgraph dev`, then run:

```bash
git status --short
```

Expected: no Studio state, trace, API key, `.env`, database, or generated output is
tracked. Remove only disposable LangGraph runtime cache files after confirming they are
generated and gitignored; do not delete any user-authored file.

- [ ] **Step 5: Run final verification**

Run:

```bash
uv run pytest -q
uv run python -m eval manifest verify --manifest eval/datasets/manifest.json
uv run python -m nlq schema check \
  --manifest eval/datasets/manifest.json \
  --dictionary nlq/schema_dictionary.json
git diff --check
```

Expected: all tests pass, the frozen manifest verifies, the six-table schema context
verifies, and the diff check is clean.

- [ ] **Step 6: Update roadmap only after the walkthrough succeeds**

Mark Phase 3d `done` and record:

- The observed full-suite test count.
- Real-model Studio success using the configured provider.
- Successful SQL and decline paths.
- LangSmith tracing disabled.

Leave Phase 3e pending because formal three-trial real-model evaluation is separate.

- [ ] **Step 7: Review checkpoint and final Phase 3d commit**

Show the user the final roadmap diff and verification output.

Proposed commit:

```text
Complete Phase 3d LangGraph pipeline
```

Stop for approval before committing. Do not begin Phase 3e in this plan.

---

## Final Review Checklist

- [ ] Every graph-state value shown in Studio is JSON-compatible.
- [ ] Router sees the question but not schema context or hidden tables.
- [ ] Generator sees the verified schema context but no database connection.
- [ ] SQLGlot validation controls the correction edge.
- [ ] SQLite remains read-only and independently authorizes execution.
- [ ] At most three SQL-generation attempts occur.
- [ ] Model transport failure does not start a duplicate graph retry loop.
- [ ] Automated tests make zero real model calls.
- [ ] Studio walkthrough uses the configured real model.
- [ ] LangSmith tracing and CLI analytics are disabled for the walkthrough.
- [ ] Phase 3a gold SQL and evaluator output never enter prompts or graph state.
- [ ] Phase 3e remains pending.
