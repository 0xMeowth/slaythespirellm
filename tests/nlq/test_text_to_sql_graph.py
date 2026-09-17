import duckdb
import pytest
from langchain_core.messages import AIMessage

from nlq.schema_context import (
    SchemaContext,
    inspect_approved_schema,
    render_schema_context,
)
from nlq.text_to_sql_graph import (
    build_text_to_sql_graph,
    run_text_to_sql_question,
)


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


@pytest.fixture
def schema_context(analytical_database, dictionary, manifest) -> SchemaContext:
    schema = inspect_approved_schema(analytical_database)
    return render_schema_context(schema, dictionary, manifest)


def test_runs_sql_route_to_first_attempt_success(
    analytical_database, schema_context
):
    with duckdb.connect(str(analytical_database)) as connection:
        connection.execute(
            """
            INSERT INTO runs(run_id, character, win, was_abandoned, ascension)
            VALUES ('run-1', 'SILENT', 1, 0, 0)
            """
        )
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
        max_attempts=3,
    )

    result = run_text_to_sql_question(
        graph, "How many completed runs are stored?"
    )

    assert result.status == "succeeded"
    assert result.attempt_count == 1
    assert result.columns == ("run_count",)
    assert result.rows == ((1,),)
    assert len(model.requests) == 2


def test_compiled_graph_accepts_question_only_input(
    analytical_database, schema_context
):
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            "SELECT COUNT(*) FROM runs",
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    final_state = graph.invoke({"question": "How many runs are stored?"})

    assert final_state["status"] == "succeeded"
    assert final_state["attempt_count"] == 1


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
        max_attempts=3,
    )

    result = run_text_to_sql_question(graph, "How should I build Silent?")

    assert result.status == "declined"
    assert result.route == "decline"
    assert result.attempt_count == 0
    assert len(model.requests) == 1


def test_router_receives_question_without_schema_and_generator_receives_both(
    analytical_database, schema_context
):
    question = "How many completed runs are stored?"
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            "SELECT COUNT(*) FROM runs",
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    run_text_to_sql_question(graph, question)

    router_prompt = _request_text(model.requests[0])
    generator_prompt = _request_text(model.requests[1])
    assert question in router_prompt
    assert schema_context.text not in router_prompt
    assert question in generator_prompt
    assert schema_context.text in generator_prompt
    assert "DuckDB" in generator_prompt
    assert "SQLite" not in generator_prompt
    for hidden_table in ("raw_runs", "sync_state", "sync_log"):
        assert hidden_table not in router_prompt
        assert hidden_table not in generator_prompt


def test_graph_exposes_named_pipeline_nodes(analytical_database, schema_context):
    graph = build_text_to_sql_graph(
        model=ScriptedChatModel([]),
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    assert {
        "route_question",
        "generate_sql",
        "validate_sql",
        "execute_sql",
        "retry_or_finish",
    }.issubset(graph.get_graph().nodes)


def test_malformed_router_response_fails_without_sql_generation(
    analytical_database, schema_context
):
    model = ScriptedChatModel(["sql"])
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    result = run_text_to_sql_question(graph, "How many runs are stored?")

    assert result.status == "failed"
    assert result.error_category == "router_output_error"
    assert result.attempt_count == 0
    assert len(model.requests) == 1


def test_retries_sql_rejected_by_validation(analytical_database, schema_context):
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            "SELECT COUNT(*) FROM raw_runs",
            "SELECT COUNT(*) AS run_count FROM runs",
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    result = run_text_to_sql_question(graph, "How many runs are stored?")

    assert result.status == "succeeded"
    assert result.generated_sql == "SELECT COUNT(*) AS run_count FROM runs"
    assert result.attempt_count == 2
    correction_prompt = _request_text(model.requests[2])
    assert "Previous SQL: SELECT COUNT(*) FROM raw_runs" in correction_prompt
    assert "Error category: unapproved_table" in correction_prompt
    assert "Error message: unapproved table: raw_runs" in correction_prompt
    assert "Traceback" not in correction_prompt
    assert "API_KEY" not in correction_prompt


def test_retries_sql_rejected_during_execution(
    analytical_database, schema_context
):
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            "SELECT missing_column FROM runs",
            "SELECT COUNT(*) AS run_count FROM runs",
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    result = run_text_to_sql_question(graph, "How many runs are stored?")

    assert result.status == "succeeded"
    assert result.attempt_count == 2
    correction_prompt = _request_text(model.requests[2])
    assert "Error category: execution_error" in correction_prompt
    assert "Error message: DuckDB could not execute the query" in correction_prompt


def test_stops_after_maximum_sql_attempts(analytical_database, schema_context):
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            "SELECT * FROM raw_runs",
            "SELECT * FROM sync_state",
            "SELECT * FROM sync_log",
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    result = run_text_to_sql_question(graph, "How many runs are stored?")

    assert result.status == "failed"
    assert result.generated_sql == "SELECT * FROM sync_log"
    assert result.attempt_count == 3
    assert result.error_category == "unapproved_table"
    assert len(model.requests) == 4


def test_retries_malformed_generated_output(analytical_database, schema_context):
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            "Here is the SQL query you requested.",
            "SELECT COUNT(*) FROM runs",
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    result = run_text_to_sql_question(graph, "How many runs are stored?")

    assert result.status == "succeeded"
    assert result.attempt_count == 2
    assert "Error category: model_output_error" in _request_text(model.requests[2])


def test_model_request_error_is_terminal(analytical_database, schema_context):
    model = ScriptedChatModel(
        [
            '{"route":"sql","reason":"stored aggregate"}',
            RuntimeError("provider failure containing a secret"),
        ]
    )
    graph = build_text_to_sql_graph(
        model=model,
        schema_context=schema_context,
        database=analytical_database,
        max_attempts=3,
    )

    result = run_text_to_sql_question(graph, "How many runs are stored?")

    assert result.status == "failed"
    assert result.attempt_count == 1
    assert result.error_category == "model_request_error"
    assert result.error_message == "model request failed"
    assert len(model.requests) == 2


def test_rejects_nonpositive_maximum_attempts(analytical_database, schema_context):
    with pytest.raises(ValueError, match="max_attempts must be positive"):
        build_text_to_sql_graph(
            model=ScriptedChatModel([]),
            schema_context=schema_context,
            database=analytical_database,
            max_attempts=0,
        )


def _request_text(messages) -> str:
    return "\n".join(str(message.content) for message in messages)
