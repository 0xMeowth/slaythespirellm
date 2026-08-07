import json
from pathlib import Path

from eval.models import ComparisonSpec, EvalCase

VALID_ROUTES = {"sql", "decline", "clarify"}
VALID_MODES = {"scalar", "ordered_rows", "unordered_rows"}
CASE_FIELDS = {
    "id",
    "question",
    "expected_route",
    "gold_sql",
    "comparison",
    "tags",
}
COMPARISON_FIELDS = {"mode", "float_tolerance"}


def load_cases(path: Path) -> list[EvalCase]:
    raw_cases = json.loads(path.read_text())
    if not isinstance(raw_cases, list):
        raise ValueError("case file must contain a JSON array")

    cases = [_parse_case(raw) for raw in raw_cases]
    seen_ids: set[str] = set()
    for case in cases:
        if case.id in seen_ids:
            raise ValueError(f"duplicate case id: {case.id}")
        seen_ids.add(case.id)
    return cases


def _parse_case(raw: object) -> EvalCase:
    if not isinstance(raw, dict):
        raise ValueError("each case must be a JSON object")

    unknown_fields = set(raw) - CASE_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown case fields: {names}")

    case_id = _required_text(raw, "id")
    question = _required_text(raw, "question")
    route = raw.get("expected_route")
    if route not in VALID_ROUTES:
        raise ValueError(f"invalid expected_route for {case_id}")

    tags = raw.get("tags")
    if (
        not isinstance(tags, list)
        or not tags
        or any(not isinstance(tag, str) or not tag.strip() for tag in tags)
    ):
        raise ValueError(f"tags must be a non-empty string array for {case_id}")

    if route == "sql":
        gold_sql = _required_text(raw, "gold_sql")
        comparison = _parse_comparison(raw.get("comparison"), case_id)
    else:
        if "gold_sql" in raw:
            raise ValueError(f"{case_id} must not include gold_sql")
        if "comparison" in raw:
            raise ValueError(f"{case_id} must not include comparison")
        gold_sql = None
        comparison = None

    return EvalCase(
        id=case_id,
        question=question,
        expected_route=route,
        gold_sql=gold_sql,
        comparison=comparison,
        tags=tuple(tags),
    )


def _required_text(raw: dict[str, object], field: str) -> str:
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _parse_comparison(raw: object, case_id: str) -> ComparisonSpec:
    if not isinstance(raw, dict):
        raise ValueError(f"comparison is required for {case_id}")

    unknown_fields = set(raw) - COMPARISON_FIELDS
    if unknown_fields:
        names = ", ".join(sorted(unknown_fields))
        raise ValueError(f"unknown comparison fields: {names}")

    mode = raw.get("mode")
    if mode not in VALID_MODES:
        raise ValueError(f"invalid comparison mode for {case_id}")

    tolerance = raw.get("float_tolerance", 0.0001)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)):
        raise ValueError(f"float_tolerance must be numeric for {case_id}")
    if tolerance < 0:
        raise ValueError(f"float_tolerance must not be negative for {case_id}")

    return ComparisonSpec(mode=mode, float_tolerance=float(tolerance))
