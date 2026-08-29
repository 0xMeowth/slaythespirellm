# Phase 3d — LangGraph Pipeline

## Goal

Build the first real text-to-SQL pipeline as an explicit LangGraph `StateGraph`.
The graph routes a question, asks SEA-LION to generate SQL, validates and executes the
SQL through Phase 3c, and retries generation with safe error feedback when needed.

Phase 3d must also provide one required LangGraph Studio walkthrough so the graph,
chosen path, intermediate state, generated SQL, errors, retries, and result rows can be
inspected visually with the real SEA-LION model.

## Scope

Phase 3d includes:

- An explicit `StateGraph` with named nodes and conditional edges.
- Typed, JSON-compatible per-question graph state.
- LLM-based question routing for SQL-answerable versus declined questions.
- LLM-based SQL generation using the verified Phase 3b schema context.
- Deterministic SQL validation and restricted execution through Phase 3c.
- Safe correction feedback and no more than three SQL-generation attempts.
- A small public result object for later Phase 3e evaluation integration.
- Deterministic automated tests using fake chat models.
- A manual LangGraph Studio walkthrough using the real SEA-LION model.

Phase 3d excludes:

- Natural-language answer synthesis, implemented in Phase 3f.
- Formal multi-case and three-trial model evaluation, implemented in Phase 3e.
- Langfuse tracing and experiment dashboards, implemented in Phase 3g.
- Entity linking, few-shot retrieval, and semantic-view experiments, implemented in
  Phase 3h.
- Conversation memory and follow-up-question resolution.
- Production deployment and operating-system isolation, implemented in Phase 4b.

## Approach Decision

LangGraph provides two primary workflow APIs:

1. The Functional API expresses control flow through ordinary Python functions,
   `if` statements, and loops. It uses less framework-specific code, but its dynamic
   structure is not available as the same complete static graph visualization.
2. The Graph API uses `StateGraph` to declare shared state, nodes, and edges. It needs
   more explicit code, but makes every production transition inspectable and easy to
   visualize.

LangChain's higher-level `create_agent` was also considered. It provides a generic
model-and-tools loop in which the model chooses tools. That abstraction gives the model
more control than this project needs and hides the specific validation and retry flow
the user wants to study.

Phase 3d uses the Graph API with an explicit `StateGraph` because:

- The user wants to learn LangGraph by viewing the actual DAG.
- SQL validation and execution must remain deterministic Python decisions.
- The model must never receive an unrestricted database tool.
- Every node should be independently testable.
- Retry state and terminal failure conditions should be visible in Studio.

References:

- https://docs.langchain.com/oss/python/langgraph/graph-api
- https://docs.langchain.com/oss/python/langgraph/functional-api
- https://docs.langchain.com/oss/python/langgraph/studio

## Dependencies

Add `langgraph` as a project dependency because production graph invocation needs its
runtime. Add `langgraph-cli[inmem]` to the development dependency group because Studio
uses the local in-memory Agent Server during development.

Do not add LangSmith tracing or Langfuse dependencies in Phase 3d. Studio is used with
LangSmith tracing disabled; persistent observability remains Phase 3g.

## Graph

The graph is:

```text
START
  |
  v
route_question
  |-- decline or router error --------------> END
  |
  v
generate_sql
  |-- malformed output --> retry_or_finish -- retry --> generate_sql
  |                              |
  |                              `-- exhausted --> END
  |
  v
validate_sql
  |-- rejected --> retry_or_finish -- retry --> generate_sql
  |                         |
  |                         `-- exhausted --> END
  v
execute_sql
  |-- failed ----> retry_or_finish -- retry --> generate_sql
  |                         |
  |                         `-- exhausted --> END
  v
END
```

The graph contains no generic tool-calling loop. LangGraph decides which Python node
runs; SEA-LION only returns a route decision or SQL text.

## State

Use a `TypedDict` state whose values remain JSON-compatible so Studio can display them
clearly:

```python
class TextToSqlState(TypedDict):
    question: str
    route: Literal["sql", "decline"] | None
    route_reason: str | None
    generated_sql: str | None
    attempt_count: int
    error_category: str | None
    error_message: str | None
    columns: list[str]
    rows: list[list[JsonScalar]]
    result_truncated: bool
    status: Literal["running", "succeeded", "declined", "failed"]
```

`JsonScalar` is `str | int | float | bool | None`. SQLite tuples are converted to lists
before entering graph state.

The state stores safe error categories and messages, not exception objects, tracebacks,
database connections, API keys, or model-client objects. Runtime dependencies are
captured by the graph's node functions when the graph is built.

The initial state is created by one helper rather than manually assembled by each
caller. It sets `attempt_count=0`, empty result lists, and `status="running"`.

## Node Responsibilities

### `route_question`

Send the user question to SEA-LION with a short routing system prompt. The router
returns strict JSON in one of these forms:

```json
{"route": "sql", "reason": "answerable from stored run statistics"}
```

```json
{"route": "decline", "reason": "strategy advice is outside the statistical database scope"}
```

Valid routes are only `sql` and `decline` in v1.

- `sql`: the question asks for facts or aggregates answerable from the approved SQLite
  schema.
- `decline`: strategy advice, opinions, predictions, unrelated requests, or questions
  that require unavailable data.

The router does not receive gold SQL, evaluation labels, database rows, or hidden table
names. A declined route sets `status="declined"` and ends without touching SQLite.

Malformed router output produces a terminal `router_output_error`. LangChain's existing
transport retries may retry network failures, but router failures do not consume the
three SQL-generation attempts.

### `generate_sql`

Increment `attempt_count`, then call SEA-LION with:

- A system prompt defining the text-to-SQL task and output rules.
- The verified, cached Phase 3b schema context.
- The original user question.
- On retries only: the previous SQL plus the safe Phase 3c error category and message.

The model must return one SQLite query as plain text. One surrounding `sql` Markdown
code fence is accepted because instruction-tuned models commonly add it. Additional
prose, multiple code blocks, or empty content is rejected as `model_output_error` and
sent through the same retry path.

If a generation request still fails after LangChain's configured transport retries,
set terminal `model_request_error` and end. A second LangGraph retry layer for transport
failures would duplicate `STS2_LLM_MAX_RETRIES`; the three-attempt graph loop is reserved
for correcting model-produced SQL.

The prompt tells the model to:

- Use only the provided schema.
- Generate one read-only SQLite query.
- Exclude abandoned runs for normal run and win-rate questions unless the user asks
  otherwise.
- Avoid inventing tables, columns, functions, or values.
- Return SQL only.

The model never sees a callable SQLite tool and cannot directly execute its output.

### `validate_sql`

Pass `generated_sql` to `nlq.sql_policy.validate_sql()`.

On success, clear the previous error and continue to execution. On
`SqlGuardrailError`, store only its safe `category` and message, then route to
`retry_or_finish`.

### `execute_sql`

Pass the SQL to `nlq.sql_executor.guard_and_execute_sql()` with the frozen evaluation
database during Phase 3d development.

`guard_and_execute_sql()` deliberately validates again before opening SQLite. The
earlier graph validation controls branching and correction feedback; the executor's
second validation preserves Phase 3c as the final security boundary even if graph code
changes later.

On success, store columns, JSON-compatible rows, truncation status, and
`status="succeeded"`. On `SqlGuardrailError`, store its safe category and message and
route to `retry_or_finish`.

### `retry_or_finish`

This node does not call the model.

- If `attempt_count < 3`, keep `status="running"` and return to `generate_sql`.
- If `attempt_count == 3`, set `status="failed"` and end.

The maximum is three SQL-generation attempts, not three LangGraph executions. Model SDK
transport retries controlled by `STS2_LLM_MAX_RETRIES` do not increment
`attempt_count`.

## Model Output Handling

Do not depend on autonomous tool calling. SEA-LION's hosted models and versions differ
in how they represent tool calls, while this graph needs only two narrow outputs.

Use ordinary chat-model responses and deterministic Python parsers:

- Router response: strict JSON parsed with `json.loads()` and exact route validation.
- Generator response: plain SQL or one surrounding `sql` code fence.

The parsers never evaluate Python, execute model-produced function arguments, or accept
arbitrary tool names.

## Public Interface

The graph builder accepts dependencies explicitly:

```python
build_text_to_sql_graph(
    *,
    model: BaseChatModel,
    schema_context: SchemaContext,
    database: Path,
    max_attempts: int = 3,
    execution_limits: ExecutionLimits = ExecutionLimits(),
) -> CompiledStateGraph
```

A plain wrapper invokes the graph and converts final state into an immutable result:

```python
run_text_to_sql_question(
    graph: CompiledStateGraph,
    question: str,
) -> TextToSqlRunResult
```

`TextToSqlRunResult` exposes:

```text
route
generated_sql
attempt_count
status
error_category
error_message
columns
rows
result_truncated
```

Phase 3e will adapt this result to the existing offline evaluator's predictor contract.
The Phase 3d graph must not import evaluation cases or gold SQL.

## Files

Phase 3d adds:

```text
nlq/text_to_sql_state.py
nlq/text_to_sql_model_output.py
nlq/text_to_sql_graph.py
nlq/studio_graph.py
langgraph.json
tests/nlq/test_text_to_sql_state.py
tests/nlq/test_text_to_sql_model_output.py
tests/nlq/test_text_to_sql_graph.py
```

It updates:

```text
pyproject.toml
uv.lock
ROADMAP.md
```

`text_to_sql_model_output.py` owns the router and SQL response parsers.
`text_to_sql_graph.py` owns prompts, nodes, edges, graph construction, and the public
invocation wrapper. `studio_graph.py` loads the existing environment-backed model,
verified schema context, and frozen database through a no-argument graph factory used
by the local Agent Server.

## LangGraph Studio Walkthrough

Studio support is a required Phase 3d deliverable, not an optional later enhancement.

Add `langgraph-cli[inmem]` to the development dependency group and a root
`langgraph.json` similar to:

```json
{
  "$schema": "https://langgra.ph/schema.json",
  "dependencies": ["."],
  "graphs": {
    "sts2_text_to_sql": "./nlq/studio_graph.py:create_studio_graph"
  },
  "env": ".env"
}
```

The manual walkthrough is one explicit verification step:

1. Set `LANGSMITH_TRACING=false` and `LANGGRAPH_CLI_NO_ANALYTICS=1` locally.
2. Load the existing SEA-LION settings from the gitignored `.env` file.
3. Run `uv run langgraph dev` from the project root.
4. Open the Studio URL printed by the command.
5. Select `sts2_text_to_sql` and submit a real statistical question.
6. Inspect the rendered DAG and each visited node's state, including generated SQL,
   validation outcome, attempt count, and result rows.
7. Submit one strategy question and confirm the graph follows the decline edge without
   executing SQL.

This uses the real SEA-LION API. It is separate from automated tests and may incur API
usage. With LangSmith tracing disabled, Studio connects to the local Agent Server
without storing application traces in LangSmith. SEA-LION still receives the prompts
because model inference is remote.

## Automated Testing

Automated tests never call SEA-LION or require network access.

Use fake chat models with scripted responses to cover:

- SQL route followed by first-attempt success.
- Declined route ending before generation or SQLite access.
- Validation rejection followed by corrected SQL.
- Execution failure followed by corrected SQL.
- Three failed generation attempts ending honestly.
- Malformed router JSON.
- Empty, prose-wrapped, and multi-block generator output.
- Attempt counting independent from model transport retries.
- Phase 3b schema context included in generation prompts.
- Phase 3c safe error feedback included only on correction attempts.
- API keys, raw exceptions, gold SQL, and hidden-table schema descriptions absent from
  prompts and state. A model-produced hidden-table name may reappear only inside the
  rejected SQL and its safe correction feedback.
- Compiled graph contains the expected named nodes and conditional paths.

Graph tests use temporary SQLite databases with the six analytical tables. Existing
Phase 3b and Phase 3c tests remain unchanged and must continue to pass.

## Real-Model Boundary

Phase 3d performs a small real-model integration test through Studio. This catches
provider-specific response formatting, prompt-following, and API compatibility problems
that fake models cannot reveal.

Phase 3e is the formal real-model evaluation phase:

```text
Phase 3d graph
    -> Phase 3a offline evaluator
    -> real SEA-LION
    -> 3 independent trials per case
```

For the initial ten development cases, Phase 3e therefore performs thirty independent
real pipeline runs. Fake models remain in unit tests because they make failures fast,
deterministic, and reproducible.

## Security and Privacy

- Treat all model output as untrusted text.
- Only Phase 3c may authorize and execute generated SQL.
- Do not expose hidden schemas, API keys, gold SQL, or evaluator outputs to the model.
- Do not send application traces to LangSmith during the Studio walkthrough.
- Do not add Langfuse in Phase 3d; its self-hosted versus managed deployment is decided
  in Phase 3g.
- Deployment-level filesystem, process, and network isolation remains mandatory in
  Phase 4b.

## Completion Criteria

Phase 3d is complete when:

- The explicit `StateGraph` implements every documented node and edge.
- All graph-path and parser tests pass without network calls.
- The complete repository test suite passes.
- A real SEA-LION statistical question succeeds through Studio.
- Studio visibly renders the graph and visited node state.
- A real strategy question follows the decline path without SQLite execution.
- No LangSmith application trace is created during the walkthrough.
- The Phase 3e evaluator handoff can consume `TextToSqlRunResult` without reading graph
  internals.
