from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict


JsonScalar: TypeAlias = str | int | float | bool | None
Route: TypeAlias = Literal["sql", "decline"]
RunStatus: TypeAlias = Literal["running", "succeeded", "declined", "failed"]


class TextToSqlInput(TypedDict):
    question: str


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


def create_initial_state(question: str) -> TextToSqlState:
    if not question.strip():
        raise ValueError("question must be non-blank")
    return {
        "question": question,
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


def create_run_result(state: TextToSqlState) -> TextToSqlRunResult:
    return TextToSqlRunResult(
        route=state["route"],
        route_reason=state["route_reason"],
        generated_sql=state["generated_sql"],
        attempt_count=state["attempt_count"],
        error_category=state["error_category"],
        error_message=state["error_message"],
        columns=tuple(state["columns"]),
        rows=tuple(tuple(row) for row in state["rows"]),
        result_truncated=state["result_truncated"],
        status=state["status"],
    )
