from pathlib import Path
from typing import cast

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from nlq.schema_context import SchemaContext
from nlq.sql_executor import ExecutionLimits, guard_and_execute_sql
from nlq.sql_policy import SqlGuardrailError, validate_sql
from nlq.text_to_sql_model_output import (
    ModelOutputError,
    parse_generated_sql,
    parse_route_response,
)
from nlq.text_to_sql_state import (
    TextToSqlRunResult,
    TextToSqlState,
    create_initial_state,
    create_run_result,
)


ROUTER_SYSTEM_PROMPT = """Classify whether the question can be answered from a statistical SQLite database.
Return exactly one JSON object with keys route and reason.
Use route sql for database facts and aggregates.
Use route decline for strategy, advice, opinion, prediction, off-topic questions, or unavailable data.
The route value must be sql or decline."""

SQL_SYSTEM_PROMPT = """Return exactly one read-only SQLite SELECT query and no explanation.
Use only the supplied schema.
Exclude rows where was_abandoned = 1 from normal run and win-rate analysis unless explicitly requested.
Never invent tables, columns, functions, or values."""


def build_text_to_sql_graph(
    *,
    model: BaseChatModel,
    schema_context: SchemaContext,
    database: Path,
    execution_limits: ExecutionLimits = ExecutionLimits(),
) -> CompiledStateGraph:
    def route_question(state: TextToSqlState) -> dict:
        try:
            response = model.invoke(
                [
                    SystemMessage(content=ROUTER_SYSTEM_PROMPT),
                    HumanMessage(content=f"Question:\n{state['question']}"),
                ]
            )
            decision = parse_route_response(response.content)
        except ModelOutputError as error:
            return {
                "error_category": error.category,
                "error_message": str(error),
                "status": "failed",
            }
        except Exception:
            return {
                "error_category": "model_request_error",
                "error_message": "model request failed",
                "status": "failed",
            }
        return {
            "route": decision.route,
            "route_reason": decision.reason,
            "status": "declined" if decision.route == "decline" else "running",
        }

    def generate_sql(state: TextToSqlState) -> dict:
        attempt_count = state["attempt_count"] + 1
        try:
            response = model.invoke(
                [
                    SystemMessage(
                        content=(
                            f"{SQL_SYSTEM_PROMPT}\n\n"
                            f"Schema:\n{schema_context.text}"
                        )
                    ),
                    HumanMessage(content=f"Question:\n{state['question']}"),
                ]
            )
            generated_sql = parse_generated_sql(response.content)
        except ModelOutputError as error:
            return {
                "attempt_count": attempt_count,
                "error_category": error.category,
                "error_message": str(error),
            }
        except Exception:
            return {
                "attempt_count": attempt_count,
                "error_category": "model_request_error",
                "error_message": "model request failed",
            }
        return {
            "generated_sql": generated_sql,
            "attempt_count": attempt_count,
            "error_category": None,
            "error_message": None,
        }

    def validate_generated_sql(state: TextToSqlState) -> dict:
        try:
            validate_sql(cast(str, state["generated_sql"]))
        except SqlGuardrailError as error:
            return {
                "error_category": error.category,
                "error_message": str(error),
            }
        return {"error_category": None, "error_message": None}

    def execute_generated_sql(state: TextToSqlState) -> dict:
        try:
            result = guard_and_execute_sql(
                database,
                cast(str, state["generated_sql"]),
                limits=execution_limits,
            )
        except SqlGuardrailError as error:
            return {
                "error_category": error.category,
                "error_message": str(error),
            }
        return {
            "columns": list(result.columns),
            "rows": [list(row) for row in result.rows],
            "result_truncated": result.truncated,
            "status": "succeeded",
        }

    def retry_or_finish(state: TextToSqlState) -> dict:
        return {"status": "failed"}

    graph = StateGraph(TextToSqlState)
    graph.add_node("route_question", route_question)
    graph.add_node("generate_sql", generate_sql)
    graph.add_node("validate_sql", validate_generated_sql)
    graph.add_node("execute_sql", execute_generated_sql)
    graph.add_node("retry_or_finish", retry_or_finish)
    graph.add_edge(START, "route_question")
    graph.add_conditional_edges(
        "route_question",
        lambda state: "generate_sql"
        if state["status"] == "running"
        else END,
    )
    graph.add_conditional_edges(
        "generate_sql",
        lambda state: "retry_or_finish"
        if state["error_category"] is not None
        else "validate_sql",
    )
    graph.add_conditional_edges(
        "validate_sql",
        lambda state: "retry_or_finish"
        if state["error_category"] is not None
        else "execute_sql",
    )
    graph.add_conditional_edges(
        "execute_sql",
        lambda state: END
        if state["status"] == "succeeded"
        else "retry_or_finish",
    )
    graph.add_edge("retry_or_finish", END)
    return graph.compile()


def run_text_to_sql_question(
    graph: CompiledStateGraph, question: str
) -> TextToSqlRunResult:
    final_state = cast(TextToSqlState, graph.invoke(create_initial_state(question)))
    return create_run_result(final_state)
