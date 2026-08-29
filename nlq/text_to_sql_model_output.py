import json
import re
from dataclasses import dataclass

from nlq.text_to_sql_state import Route


_FENCED_SQL = re.compile(
    r"```sql[ \t]*\r?\n(?P<sql>.*?)\r?\n```",
    re.IGNORECASE | re.DOTALL,
)
_QUERY_START = re.compile(r"(?:SELECT|WITH)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RouteDecision:
    route: Route
    reason: str


class ModelOutputError(ValueError):
    def __init__(self, category: str, message: str) -> None:
        super().__init__(message)
        self.category = category


def parse_route_response(text: str) -> RouteDecision:
    try:
        parsed_route_response = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        raise ModelOutputError(
            "router_output_error", "router response must be valid JSON"
        ) from None
    if not isinstance(parsed_route_response, dict) or set(parsed_route_response) != {
        "route",
        "reason",
    }:
        raise ModelOutputError(
            "router_output_error", "router response has invalid fields"
        )
    route = parsed_route_response["route"]
    reason = parsed_route_response["reason"]
    if route not in {"sql", "decline"}:
        raise ModelOutputError(
            "router_output_error", "router response has an invalid route"
        )
    if not isinstance(reason, str) or not reason.strip():
        raise ModelOutputError(
            "router_output_error", "router response reason must be non-blank"
        )
    return RouteDecision(route=route, reason=reason.strip())


def parse_generated_sql(text: str) -> str:
    candidate = text.strip()
    fenced = _FENCED_SQL.fullmatch(candidate)
    if fenced is not None:
        candidate = fenced.group("sql").strip()
        if "```" in candidate:
            raise ModelOutputError(
                "model_output_error", "generated SQL has invalid Markdown fences"
            )
    elif "```" in candidate:
        raise ModelOutputError(
            "model_output_error", "generated SQL has invalid Markdown fences"
        )
    if not candidate or _QUERY_START.match(candidate) is None:
        raise ModelOutputError(
            "model_output_error", "model response must contain SQL only"
        )
    return candidate
