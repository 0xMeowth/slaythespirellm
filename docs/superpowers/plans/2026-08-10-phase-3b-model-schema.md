# Phase 3b Model and Schema Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect LangChain to configurable GLM and SEA-LION OpenAI-compatible endpoints and build a verified, deterministic, cached prompt context for the six analytical SQLite tables.

**Architecture:** Keep model configuration, model construction, dictionary parsing, SQLite inspection, and context rendering in focused modules. Validate all environment values and schema metadata before model use, render only the shared six-table allowlist, and expose manual CLI checks while keeping automated tests network-free.

**Tech Stack:** Python 3.13+, uv, pytest, stdlib `argparse`, `dataclasses`, `hashlib`, `json`, `sqlite3`, and `time`, plus `langchain-openai`.

## Global Constraints

- Follow `docs/specs/phase-3b-model-schema.md`; update it when implementation changes a documented decision.
- Add `langchain-openai`; do not add LangGraph, SQLGlot, Langfuse, DSPy, or provider SDKs.
- Use only `runs`, `run_cards`, `run_relics`, `run_card_choices`, `cards`, and `relics` in model schema context.
- Never inspect, render, sample, or mention `raw_runs`, `sync_state`, `sync_log`, or `sqlite_*` tables in model messages.
- Open SQLite read-only for schema inspection.
- Parse configuration into frozen dataclasses and never expose `STS2_LLM_API_KEY` in representations, errors, logs, or CLI output.
- Set `temperature=0`, `max_tokens=1024`, `timeout=60`, `max_retries=2`, `n=1`, and `streaming=false` by default.
- Keep automated tests deterministic and network-free; the real connectivity check is manual and opt-in.
- Do not include full database sample rows in the baseline schema context.
- Show every proposed commit message to the user before committing and never add a `Co-Authored-By` trailer.

## File Map

- `nlq/model_settings.py` — immutable environment parsing and non-secret report settings.
- `nlq/model_client.py` — provider-specific request bodies, `ChatOpenAI` construction, and connectivity response parsing.
- `nlq/schema_dictionary.json` — reviewed meanings, domain values, ranges, examples, and joins for every approved column.
- `nlq/schema_dictionary.py` — strict JSON dictionary models and parsing.
- `nlq/schema_context.py` — allowlist, read-only SQLite inspection, dictionary validation, deterministic rendering, hashing, and in-memory cache.
- `nlq/__main__.py` — `model check` and `schema check` commands.
- `tests/nlq/` — focused settings, model, dictionary, context, cache, and CLI tests.
- `pyproject.toml`, `uv.lock` — LangChain dependency.
- `ROADMAP.md` — Phase 3b completion evidence.

---

### Task 1: Model Settings and Provider Profiles

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Create: `nlq/model_settings.py`
- Create: `nlq/model_client.py`
- Create: `tests/nlq/test_model_settings.py`
- Create: `tests/nlq/test_model_client.py`

**Interfaces:**
- Consumes: `Mapping[str, str]` environment values.
- Produces: `ModelSettings.from_environment(environment)`, `ModelSettings.report_values()`, `provider_extra_body(settings)`, and `build_chat_model(settings, model_class=ChatOpenAI)`.

- [ ] **Step 1: Add LangChain's OpenAI-compatible integration**

Run:

```bash
uv add langchain-openai
```

Expected: `langchain-openai` appears in project dependencies and `uv.lock` resolves its transitive dependencies.

- [ ] **Step 2: Write failing settings tests**

Cover valid defaults and overrides, every missing required variable, unknown providers, unsupported thinking modes, invalid booleans, non-numeric values, negative retries, non-positive token/time limits, and API-key redaction.

```python
def test_loads_defaults(valid_environment):
    settings = ModelSettings.from_environment(valid_environment)
    assert settings.temperature == 0
    assert settings.max_tokens == 1024
    assert settings.timeout_seconds == 60
    assert settings.max_retries == 2
    assert settings.disable_provider_cache is True
    assert "secret-key" not in repr(settings)
    assert "api_key" not in settings.report_values()


def test_rejects_missing_api_key(valid_environment):
    del valid_environment["STS2_LLM_API_KEY"]
    with pytest.raises(ValueError, match="STS2_LLM_API_KEY is required"):
        ModelSettings.from_environment(valid_environment)
```

- [ ] **Step 3: Run settings tests to verify failure**

Run: `uv run python -m pytest tests/nlq/test_model_settings.py -q`

Expected: FAIL because `nlq.model_settings` does not exist.

- [ ] **Step 4: Implement immutable settings parsing**

Define:

```python
Provider = Literal["glm", "sea_lion"]
ThinkingMode = Literal["enabled", "disabled", "provider_default"]

@dataclass(frozen=True)
class ModelSettings:
    provider: Provider
    base_url: str
    api_key: str = field(repr=False)
    model: str
    thinking_mode: ThinkingMode
    temperature: float = 0
    max_tokens: int = 1024
    timeout_seconds: float = 60
    max_retries: int = 2
    disable_provider_cache: bool = True
```

Read the five required `STS2_LLM_*` values and five optional values from the supplied mapping or `os.environ`. Reject blank strings, booleans other than `true`/`false`, `max_tokens < 1`, `timeout_seconds <= 0`, `max_retries < 0`, and non-finite temperature values. `report_values()` returns every non-secret setting plus fixed `n=1` and `streaming=False`.

- [ ] **Step 5: Run settings tests**

Run: `uv run python -m pytest tests/nlq/test_model_settings.py -q`

Expected: all settings tests pass.

- [ ] **Step 6: Write failing provider-profile and factory tests**

Use a recording fake model class and assert exact constructor arguments.

```python
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("enabled", {"thinking": {"type": "enabled"}}),
        ("disabled", {"thinking": {"type": "disabled"}}),
        ("provider_default", {}),
    ],
)
def test_glm_thinking_body(settings_factory, mode, expected):
    settings = settings_factory(provider="glm", thinking_mode=mode)
    assert provider_extra_body(settings) == expected


def test_sea_lion_disables_thinking_and_cache(settings_factory):
    settings = settings_factory(
        provider="sea_lion",
        thinking_mode="disabled",
        disable_provider_cache=True,
    )
    assert provider_extra_body(settings) == {
        "chat_template_kwargs": {"enable_thinking": False},
        "cache": {"no-cache": True},
    }
```

Also assert that `build_chat_model()` passes `model`, `base_url`, `api_key`, `temperature`, `max_tokens`, `timeout`, `max_retries`, `n=1`, `streaming=False`, and only includes `extra_body` when non-empty.

- [ ] **Step 7: Run model-client tests to verify failure**

Run: `uv run python -m pytest tests/nlq/test_model_client.py -q`

Expected: FAIL because `nlq.model_client` does not exist.

- [ ] **Step 8: Implement provider profiles and model construction**

`provider_extra_body(settings: ModelSettings) -> dict[str, object]` returns GLM's
`thinking.type` body or SEA-LION's `chat_template_kwargs.enable_thinking` and optional
`cache.no-cache` body. It omits thinking fields for `provider_default` and returns a
fresh dictionary.

`build_chat_model(settings: ModelSettings, model_class=ChatOpenAI) -> BaseChatModel`
constructs the injected class with the tested arguments. It does not invoke the model.

- [ ] **Step 9: Run Task 1 tests**

Run: `uv run python -m pytest tests/nlq/test_model_settings.py tests/nlq/test_model_client.py -q`

Expected: all Task 1 tests pass.

- [ ] **Step 10: Request commit approval**

Proposed commit message: `Add configurable LangChain model client`

---

### Task 2: Strict Schema Dictionary

**Files:**
- Create: `nlq/schema_dictionary.py`
- Create: `nlq/schema_dictionary.json`
- Create: `tests/nlq/test_schema_dictionary.py`

**Interfaces:**
- Consumes: the checked-in JSON dictionary or a fixture path.
- Produces: frozen `ColumnDescription`, `JoinDescription`, `TableDescription`, `SchemaDictionary`, and `load_schema_dictionary(path)`.

- [ ] **Step 1: Write failing parser tests**

Test valid loading plus malformed root values, unknown table fields, unknown column fields, blank descriptions, invalid value maps, invalid ranges, invalid examples, malformed joins, and duplicate join entries.

```python
def test_loads_typed_dictionary(dictionary_path):
    dictionary = load_schema_dictionary(dictionary_path)
    assert dictionary.tables["runs"].columns["win"].values == {
        "0": "loss",
        "1": "victory",
    }


def test_rejects_unknown_column_metadata(tmp_path):
    path = write_dictionary(
        tmp_path,
        column={"description": "Meaning", "unsupported": True},
    )
    with pytest.raises(ValueError, match="unknown column fields"):
        load_schema_dictionary(path)
```

- [ ] **Step 2: Run parser tests to verify failure**

Run: `uv run python -m pytest tests/nlq/test_schema_dictionary.py -q`

Expected: FAIL because `nlq.schema_dictionary` does not exist.

- [ ] **Step 3: Implement strict dictionary models and loading**

Define:

```python
@dataclass(frozen=True)
class ColumnDescription:
    description: str
    values: tuple[tuple[str, str], ...] = ()
    minimum: int | float | None = None
    maximum: int | float | None = None
    examples: tuple[str | int | float, ...] = ()

@dataclass(frozen=True)
class JoinDescription:
    table: str
    on: str
    description: str

@dataclass(frozen=True)
class TableDescription:
    description: str
    columns: dict[str, ColumnDescription]
    joins: tuple[JoinDescription, ...]

@dataclass(frozen=True)
class SchemaDictionary:
    tables: dict[str, TableDescription]
    sha256: str
```

The JSON root is an object keyed directly by table name. Table fields are exactly `description`, `columns`, and `joins`; column fields are exactly `description`, optional `values`, optional `range`, and optional `examples`; range fields are exactly `minimum` and `maximum`; join fields are exactly `table`, `on`, and `description`. Hash the exact file bytes with SHA-256.

- [ ] **Step 4: Populate the reviewed six-table dictionary**

Document every column from `schema.sql`:

- `runs`: one row per submitted run; explain all 18 columns, `win`, `was_abandoned`, ascension range `0–10`, representative character IDs, Unix `start_time`, ISO `submitted_at`, and valid run-level meanings.
- `run_cards`: one row per physical final-deck card copy; explain all four columns and joins to `runs.run_id` and `cards.card_id`.
- `run_relics`: one row per acquired relic; explain all three columns and joins to `runs.run_id` and `relics.relic_id`.
- `run_card_choices`: one row per offered card candidate; explain all five columns, `was_picked`, act/floor numbering, and joins to `runs.run_id` and `cards.card_id`.
- `cards`: one row per card reference entity; explain all ten columns, `is_x_cost`, representative type/rarity/color/target values, and comma-separated keywords.
- `relics`: one row per relic reference entity; explain all five columns and representative rarity/pool values.

Do not add full database rows or enumerate all card/relic IDs.

- [ ] **Step 5: Run parser tests**

Run: `uv run python -m pytest tests/nlq/test_schema_dictionary.py -q`

Expected: all dictionary parser tests pass and the checked-in dictionary loads.

- [ ] **Step 6: Request commit approval**

Proposed commit message: `Add reviewed NLQ schema dictionary`

---

### Task 3: Verified Schema Context and Cache

**Files:**
- Create: `nlq/schema_context.py`
- Create: `tests/nlq/conftest.py`
- Create: `tests/nlq/test_schema_context.py`
- Create: `tests/nlq/test_schema_cache.py`

**Interfaces:**
- Consumes: an SQLite path, `SchemaDictionary`, and `DatasetManifest`.
- Produces: `APPROVED_TABLES`, schema metadata dataclasses,
  `inspect_approved_schema(database: Path) -> DatabaseSchema`,
  `validate_dictionary(schema: DatabaseSchema, dictionary: SchemaDictionary) -> None`,
  `render_schema_context(schema, dictionary, manifest) -> SchemaContext`,
  `SchemaContextCache.get_or_build(...)`, and `build_verified_schema_context(...)`.

- [ ] **Step 1: Create a six-table SQLite test fixture**

Build a temporary database containing reduced but representative approved tables plus `raw_runs`, `sync_state`, and `sync_log`. Use real approved names and preserve `PRAGMA table_info` column order.

```python
APPROVED_TABLES = (
    "runs",
    "run_cards",
    "run_relics",
    "run_card_choices",
    "cards",
    "relics",
)
```

- [ ] **Step 2: Write failing inspection and validation tests**

Test exact allowlist order, read-only inspection, missing approved tables, ignored plumbing tables, missing dictionary tables/columns, unknown dictionary tables/columns, blank descriptions caught by parsing, invalid join targets, and invalid join expressions referencing unknown columns.

```python
def test_inspects_only_approved_tables(analytical_database):
    schema = inspect_approved_schema(analytical_database)
    assert tuple(table.name for table in schema.tables) == APPROVED_TABLES
    rendered_names = {table.name for table in schema.tables}
    assert rendered_names.isdisjoint({"raw_runs", "sync_state", "sync_log"})


def test_rejects_missing_dictionary_column(schema, dictionary_without_win):
    with pytest.raises(ValueError, match="runs missing dictionary columns: win"):
        validate_dictionary(schema, dictionary_without_win)
```

- [ ] **Step 3: Run schema tests to verify failure**

Run: `uv run python -m pytest tests/nlq/test_schema_context.py -q`

Expected: FAIL because `nlq.schema_context` does not exist.

- [ ] **Step 4: Implement read-only inspection and exact validation**

Define frozen `ColumnSchema(name, sqlite_type, nullable, primary_key)`, `TableSchema(name, columns)`, and `DatabaseSchema(tables)`. Open with `file:<absolute path>?mode=ro`, query only `PRAGMA table_info(<quoted approved name>)`, and reject absent approved tables. Compare exact table and column sets, then validate each structured join against approved tables and columns.

- [ ] **Step 5: Write failing renderer tests**

Assert stable approved-table order, live SQLite column order, types/nullability, descriptions, values/ranges/examples, joins, no plumbing names, no database rows, stable text, and stable SHA-256.

```python
def test_render_is_stable(schema, dictionary, manifest):
    first = render_schema_context(schema, dictionary, manifest)
    second = render_schema_context(schema, dictionary, manifest)
    assert first.text == second.text
    assert first.sha256 == second.sha256
    assert "Table: runs" in first.text
    assert "sync_state" not in first.text
```

- [ ] **Step 6: Implement deterministic rendering**

Define:

```python
RENDERER_VERSION = "1"

@dataclass(frozen=True)
class SchemaContext:
    text: str
    sha256: str
    dataset_id: str
    database_sha256: str
    schema_git_commit: str
    dictionary_sha256: str
    approved_tables: tuple[str, ...]
```

Render tables in `APPROVED_TABLES` order and columns in SQLite order. Sort value-map keys and joins deterministically. End the text with one newline and hash its UTF-8 bytes.

- [ ] **Step 7: Run renderer tests**

Run: `uv run python -m pytest tests/nlq/test_schema_context.py -q`

Expected: all context tests pass.

- [ ] **Step 8: Write failing cache tests**

Test one builder call for repeated identical keys and cache misses when database SHA, schema commit, dictionary SHA, or renderer version changes.

```python
def test_cache_reuses_identical_context(cache_inputs):
    calls = 0
    cache = SchemaContextCache()
    first = cache.get_or_build(**cache_inputs, builder=counting_builder)
    second = cache.get_or_build(**cache_inputs, builder=counting_builder)
    assert first is second
    assert calls == 1
```

- [ ] **Step 9: Implement startup-level in-memory caching**

Use an immutable `SchemaContextCacheKey(database_sha256, schema_git_commit, dictionary_sha256, renderer_version)` and a private dictionary guarded by a lock. `build_verified_schema_context()` verifies the supplied manifest path, loads the dictionary, computes the key, and caches inspection/validation/rendering as one build operation.

- [ ] **Step 10: Run Task 3 tests**

Run: `uv run python -m pytest tests/nlq/test_schema_context.py tests/nlq/test_schema_cache.py -q`

Expected: all Task 3 tests pass.

- [ ] **Step 11: Request commit approval**

Proposed commit message: `Add verified cached schema context`

---

### Task 4: Model and Schema Check CLI

**Files:**
- Create: `nlq/__main__.py`
- Create: `tests/nlq/test_cli.py`

**Interfaces:**
- Consumes: command arguments, environment variables, manifest/dictionary paths, and injectable model/build functions for tests.
- Produces: `main(arguments=None, *, environment=None, model_builder=build_chat_model, clock=time.perf_counter) -> int` supporting `model check` and `schema check`.

- [ ] **Step 1: Write failing schema-check CLI tests**

Test matching schema success, dictionary mismatch, manifest mismatch, default hidden context, `--show-context`, concise default errors, and `--debug` tracebacks.

```python
def test_schema_check_prints_verified_metadata(cli_fixture):
    completed = run_nlq_cli(
        "schema", "check",
        "--manifest", str(cli_fixture.manifest),
        "--dictionary", str(cli_fixture.dictionary),
    )
    assert completed.returncode == 0
    assert "Dataset: test-dataset" in completed.stdout
    assert "Approved tables: runs, run_cards" in completed.stdout
    assert "Context SHA-256:" in completed.stdout
    assert "Table: runs" not in completed.stdout
```

- [ ] **Step 2: Run schema CLI tests to verify failure**

Run: `uv run python -m pytest tests/nlq/test_cli.py -q`

Expected: FAIL because `nlq.__main__` does not exist.

- [ ] **Step 3: Implement `schema check`**

Parse:

```text
python -m nlq [--debug] schema check --manifest PATH --dictionary PATH [--show-context]
```

Load and verify the manifest against `Path.cwd()`, build the verified context, and print dataset ID, approved tables, context SHA-256, and UTF-8 byte count. Print context only when requested. Return `1` with `error: <message>` on stderr unless `--debug` re-raises.

- [ ] **Step 4: Write failing model-check CLI tests**

Inject fake models returning a LangChain `AIMessage`. Test one fixed acknowledgement request, non-empty response enforcement, provider/model/latency output, optional token usage, secret redaction, default concise errors, and debug tracebacks.

```python
def test_model_check_uses_injected_fake(valid_environment, fake_model_builder):
    exit_code = main(
        ["model", "check"],
        environment=valid_environment,
        model_builder=fake_model_builder,
        clock=iter([10.0, 10.25]).__next__,
    )
    assert exit_code == 0
    assert fake_model_builder.model.messages == [
        "Reply with exactly: connection-ok"
    ]
```

- [ ] **Step 5: Implement `model check`**

Parse:

```text
python -m nlq [--debug] model check
```

Load settings, build the model, call `invoke("Reply with exactly: connection-ok")`, require non-empty string content, and print provider, model, elapsed milliseconds, and usage metadata when present. Never print response reasoning, headers, or the API key.

- [ ] **Step 6: Run Task 4 tests**

Run: `uv run python -m pytest tests/nlq/test_cli.py -q`

Expected: all CLI tests pass without network access.

- [ ] **Step 7: Request commit approval**

Proposed commit message: `Add NLQ model and schema checks`

---

### Task 5: Real Snapshot Verification and Phase Completion

**Files:**
- Modify: `docs/specs/phase-3b-model-schema.md` only if implementation changed a decision.
- Modify: `ROADMAP.md`

**Interfaces:**
- Consumes: `eval/datasets/manifest.json`, `nlq/schema_dictionary.json`, and one configured provider environment.
- Produces: verified context metadata and recorded Phase 3b completion evidence.

- [ ] **Step 1: Run the focused NLQ suite**

Run: `uv run python -m pytest tests/nlq -q`

Expected: all Phase 3b tests pass with no credentials and no network calls.

- [ ] **Step 2: Run the full regression suite**

Run: `uv run python -m pytest -q`

Expected: all existing 65 Phase 3a offline evaluation tests plus all Phase 3b tests pass.

- [ ] **Step 3: Verify the frozen snapshot context**

Ensure the worktree's ignored `data/` path resolves to the existing external-SSD snapshot, then run:

```bash
uv run python -m nlq schema check \
  --manifest eval/datasets/manifest.json \
  --dictionary nlq/schema_dictionary.json
```

Expected: six approved tables, a stable context SHA-256, and no dictionary/schema mismatch.

- [ ] **Step 4: Run the selected provider connectivity check**

With `STS2_LLM_PROVIDER`, `STS2_LLM_BASE_URL`, `STS2_LLM_API_KEY`, `STS2_LLM_MODEL`, and `STS2_LLM_THINKING_MODE` set, run:

```bash
uv run python -m nlq model check
```

Expected: non-empty acknowledgement plus provider, model, and latency. If credentials are unavailable, record this single completion criterion as blocked rather than weakening or mocking the manual check.

- [ ] **Step 5: Update the roadmap with evidence**

Mark 3b `done` only after Step 4 succeeds. Record the tested provider/model, context SHA-256, and final automated test count. If Step 4 is blocked, leave 3b `in-progress` and record that only the live provider check remains.

- [ ] **Step 6: Run documentation and repository checks**

Run:

```bash
git diff --check
git status --short
```

Expected: no whitespace errors; only intended Phase 3b files are modified or untracked.

- [ ] **Step 7: Request final commit approval**

Proposed commit message: `Complete Phase 3b model schema foundation`
