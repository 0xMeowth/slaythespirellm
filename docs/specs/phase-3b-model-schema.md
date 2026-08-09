# Phase 3b — Model and Schema Context

## Goal

Connect LangChain to a configurable OpenAI-compatible chat model and build a verified,
cached schema context for the six analytical SQLite tables. Phase 3b proves model
connectivity and schema preparation without generating or executing production SQL.

## Scope

Phase 3b includes:

- A provider-agnostic model configuration loaded from environment variables.
- LangChain `ChatOpenAI` construction for SEA-LION and GLM-compatible endpoints.
- Provider-specific thinking-mode request settings.
- A live, opt-in model connectivity command.
- Deterministic inspection of the six approved SQLite tables.
- A checked-in, human-reviewed JSON data dictionary.
- Strict validation of the dictionary against the real SQLite schema.
- Deterministic schema-context rendering and startup-level in-memory caching.
- Unit tests using fake models; no paid or network calls in the automated test suite.

Phase 3b excludes:

- Question routing.
- SQL generation.
- SQLGlot validation and table enforcement.
- SQL execution through an agent.
- LangGraph state and correction loops.
- Answer synthesis.
- Langfuse integration.
- Few-shot question-to-SQL examples.

Those concerns remain in Phases 3c–3g.

## Dependencies

Add `langchain-openai` as the LangChain integration for OpenAI-compatible chat
completion endpoints. Do not add LangGraph in this phase.

`ChatOpenAI` is used because both candidate providers expose OpenAI-compatible chat
completion APIs. Provider-specific request fields are passed through `extra_body`.

## Model Configuration

Required environment variables:

```text
STS2_LLM_PROVIDER=glm|sea_lion
STS2_LLM_BASE_URL=<OpenAI-compatible API base URL>
STS2_LLM_API_KEY=<secret>
STS2_LLM_MODEL=<provider model identifier>
STS2_LLM_THINKING_MODE=enabled|disabled|provider_default
```

Optional variables with explicit defaults:

```text
STS2_LLM_TEMPERATURE=0
STS2_LLM_MAX_TOKENS=1024
STS2_LLM_TIMEOUT_SECONDS=60
STS2_LLM_MAX_RETRIES=2
STS2_LLM_DISABLE_PROVIDER_CACHE=true
```

Fixed baseline settings:

```text
n=1
streaming=false
```

`n=1` means one completion per API request. Three-trial evaluation later makes three
independent requests rather than requesting three candidates from one call.

`max_retries` handles transient transport, rate-limit, and provider failures. It does
not count as a new SQL-generation attempt. The LangGraph SQL correction limit remains
`max_attempts=3` and is implemented in Phase 3d.

All values are parsed into an immutable settings object. Missing required values,
unknown providers, invalid numbers, negative retries, and unsupported thinking modes
fail before constructing the model client.

The API key must never appear in logs, exceptions, CLI output, schema context, or eval
reports. Later reports record all non-secret settings, including thinking mode.

## Provider Profiles

The generic settings object is translated into provider-specific `ChatOpenAI`
arguments by a small provider-profile layer. Do not infer the provider from the URL or
model name.

### GLM

GLM thinking control is passed through:

```json
{
  "thinking": {
    "type": "enabled"
  }
}
```

Use `disabled` for non-thinking mode and omit the field for `provider_default`.

### SEA-LION

Qwen-SEA-LION-v4.5 inherits Qwen's `enable_thinking` chat-template control. The hosted
provider profile infers the request mapping by combining that documented key with
SEA-LION's documented `chat_template_kwargs`/`extra_body` API pattern:

```json
{
  "chat_template_kwargs": {
    "enable_thinking": false
  }
}
```

Use `true` for enabled mode and omit the field for `provider_default`. When
`STS2_LLM_DISABLE_PROVIDER_CACHE=true`, also send SEA-LION's documented no-cache
request option. Because the v4.5 hosted page does not show this exact payload, the live
connectivity command must verify it and fail clearly if the selected endpoint does not
accept the configured provider fields.

Do not expose a generic arbitrary `extra_body` environment variable in v1. Every
provider-specific field must be deliberate, typed, tested, and recorded.

## Model Factory

The model factory accepts the immutable settings object and returns a LangChain chat
model configured with:

```text
model
base_url
api_key
temperature
max_tokens
timeout
max_retries
n=1
streaming=false
provider-specific extra_body
```

The rest of the NLQ code depends on LangChain's chat-model interface rather than
provider SDK classes. Changing between SEA-LION and GLM requires environment changes,
not application-code changes.

The factory does not send a request while constructing the client.

## Live Connectivity Check

Provide:

```bash
uv run python -m nlq model check
```

The command:

1. Loads and validates model settings.
2. Constructs the LangChain model.
3. Sends one short request asking for a fixed acknowledgement.
4. Confirms that a non-empty text response is returned.
5. Prints provider, model, latency, and token usage when supplied by the endpoint.

It must not print the API key, full request headers, or provider reasoning content.
Network, authentication, missing-model, timeout, and malformed-response failures return
a non-zero exit status with a concise message. `--debug` enables a traceback.

This command is manual and opt-in. `pytest` never invokes it.

## Approved Schema Surface

Only these tables enter schema context:

```text
runs
run_cards
run_relics
run_card_choices
cards
relics
```

The allowlist is a code constant shared by schema inspection and later SQL guardrails.
Phase 3c will reuse it for enforcement.

The following tables must not be inspected, rendered, sampled, or named in model
messages:

```text
raw_runs
sync_state
sync_log
sqlite_* internal tables
```

The database is opened read-only. Schema inspection uses SQLite metadata such as
`PRAGMA table_info` and does not execute model-generated SQL.

## Data Dictionary

Store the human-reviewed dictionary at:

```text
nlq/schema_dictionary.json
```

Every approved table must have:

- A concise purpose description.
- An entry for every exposed column.
- A concise meaning for every column.
- Complete allowed values for small fixed domains.
- Ranges for bounded numeric domains when useful.
- A few representative examples for high-cardinality identifiers.
- Explicit join relationships that are analytically valid.

Example structure:

```json
{
  "runs": {
    "description": "One row per submitted game run.",
    "columns": {
      "win": {
        "description": "Whether the run ended in victory.",
        "values": {
          "0": "loss",
          "1": "victory"
        }
      }
    }
  }
}
```

The dictionary does not duplicate SQLite types. Types come from live schema
inspection, preventing type information from drifting in two places.

Do not include full sample rows in the Phase 3b baseline. Targeted values are more
compact and less arbitrary. Full sample rows remain a Phase 3h A/B experiment.

Do not list all 577 card IDs or 296 relic IDs. Entity resolution later injects only
question-relevant candidates.

## Dictionary Validation

At application startup:

1. Load the JSON object strictly.
2. Reject unknown top-level fields and malformed entry shapes.
3. Inspect the six approved tables through read-only SQLite.
4. Compare dictionary table names with the exact allowlist.
5. Compare dictionary column names with `PRAGMA table_info` output.
6. Reject unknown dictionary columns.
7. Reject approved SQLite columns missing from the dictionary.
8. Reject missing or blank descriptions.
9. Validate dictionary value/range/example types.

Structural validation can prove that descriptions refer to real tables and columns.
It cannot prove that English meanings are factually correct; those remain
human-reviewed data.

## Schema Context Rendering

The renderer deterministically combines:

```text
SQLite table and column names
+ SQLite column types and nullability
+ human-reviewed meanings
+ targeted values, ranges, and examples
+ valid join relationships
```

The output uses a stable table order, stable column order, and stable formatting so
identical inputs produce identical text and SHA-256 hashes.

Conceptual output:

```text
Table: runs
Purpose: One row per submitted game run.

- character TEXT NOT NULL
  Meaning: Character played by the first player.
  Values: IRONCLAD, SILENT, DEFECT, NECROBINDER, REGENT

- win INTEGER NOT NULL
  Meaning: Whether the run ended in victory.
  Values: 0 = loss; 1 = victory
```

The renderer produces a `SchemaContext` containing:

```text
text
sha256
dataset_id
database_sha256
schema_git_commit
dictionary_sha256
approved_tables
```

## Schema Context Cache

SQLite introspection and rendering happen once during application startup, not once per
question.

The in-memory cache key contains:

```text
database_sha256
schema_git_commit
dictionary_sha256
renderer_version
```

Every question reuses the cached `SchemaContext.text`. A changed database manifest,
schema commit, dictionary file, or renderer version creates a different key and forces
a rebuild.

The schema text is still included logically in every independent model request. A
provider may apply prompt caching, but provider caching is separate from this
application-level schema cache and is disabled during formal evaluations where the
provider supports that control.

## Schema Check CLI

Provide:

```bash
uv run python -m nlq schema check \
  --manifest eval/datasets/manifest.json \
  --dictionary nlq/schema_dictionary.json
```

The command:

1. Verifies the frozen database manifest.
2. Validates the dictionary against SQLite.
3. Builds the schema context.
4. Prints dataset ID, approved tables, context SHA-256, and context size.
5. Prints the rendered context only with `--show-context`.

It makes no model call.

## Testing

Automated tests use temporary SQLite fixtures and fake LangChain chat models.

Required settings tests:

- Valid environment parsing.
- Missing secrets and required values.
- Numeric bounds and enum validation.
- Secret-safe representations and errors.
- GLM and SEA-LION thinking-mode request bodies.
- Fixed `n=1` and non-streaming configuration.

Required schema tests:

- Exact six-table allowlist.
- Rejection of plumbing and unknown tables.
- Missing and unknown dictionary columns.
- Stable table and column ordering.
- Stable context hashes.
- Cache hit for identical version inputs.
- Cache miss when any key component changes.
- No full sample rows in rendered context.

Required CLI tests:

- Schema check succeeds against a matching fixture.
- Schema check fails on dictionary/schema mismatch.
- Model check uses a fake model in-process.
- Clean errors by default and tracebacks with `--debug`.
- No API key appears in captured output.

One manual live check is required before Phase 3b completes for each provider used in
later evaluation. Automated tests must remain network-free and deterministic.

## Error Handling

- Invalid environment configuration: fail before model construction.
- Missing API key: identify the environment-variable name without printing its value.
- Unsupported provider or thinking mode: fail before network access.
- Manifest mismatch: abort schema-context construction.
- Dictionary/schema mismatch: report exact missing and unknown names, then abort.
- SQLite metadata error: abort without falling back to unverified static schema.
- Model connectivity failure: non-zero CLI exit with a concise provider-safe message.
- Provider-specific request rejection: report which non-secret setting was rejected.

Do not silently drop unsupported parameters because that would make eval configurations
misleading.

## Completion Criteria

Phase 3b is complete when:

- `langchain-openai` is installed and locked by uv.
- Both provider profiles build expected request settings in unit tests.
- The frozen database and dictionary produce one stable cached schema context.
- Only six approved tables appear in that context.
- Every approved table and column has a reviewed dictionary entry.
- The schema-check CLI passes against the frozen evaluation database.
- At least the first selected real model passes the live connectivity command.
- The full automated suite passes without network access or secrets.
- `ROADMAP.md` records the tested provider, model, and context hash.

Phase 3b does not claim SQL accuracy. SQL generation and end-to-end evaluation occur in
later phases.

## Design References

- LangChain `ChatOpenAI` documents `model`, `temperature`, `max_tokens`, `timeout`,
  `max_retries`, `api_key`, and `base_url` as primary configuration parameters:
  <https://reference.langchain.com/python/langchain-openai/chat_models/base/ChatOpenAI>
- LangChain documents `extra_body` for non-standard OpenAI-compatible provider
  parameters:
  <https://reference.langchain.com/python/langchain-openai/langchain_openai>
- GLM documents `thinking.type` as `enabled` or `disabled`:
  <https://docs.z.ai/guides/capabilities/thinking-mode>
- SEA-LION documents Qwen-SEA-LION-v4.5's `enable_thinking` chat-template control:
  <https://docs.sea-lion.ai/models/sea-lion-v4.5/qwen-sea-lion-v4.5>
- SEA-LION documents hosted-API cache controls and provider-specific chat-template
  parameters:
  <https://docs.sea-lion.ai/guides/inferencing/api>
