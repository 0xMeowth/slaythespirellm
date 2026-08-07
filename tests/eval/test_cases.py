import json

import pytest

from eval.cases import load_cases


def write_cases(tmp_path, cases):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(cases))
    return path


def decline_case(case_id="strategy"):
    return {
        "id": case_id,
        "question": "How should I play Silent?",
        "expected_route": "decline",
        "tags": ["router", "strategy"],
    }


def sql_case():
    return {
        "id": "stored_run_count",
        "question": "How many runs are stored?",
        "expected_route": "sql",
        "gold_sql": "SELECT COUNT(*) FROM runs",
        "comparison": {"mode": "scalar", "float_tolerance": 0.0001},
        "tags": ["runs", "count"],
    }


def test_loads_sql_case(tmp_path):
    case = load_cases(write_cases(tmp_path, [sql_case()]))[0]

    assert case.id == "stored_run_count"
    assert case.comparison is not None
    assert case.comparison.mode == "scalar"
    assert case.tags == ("runs", "count")


def test_rejects_duplicate_case_ids(tmp_path):
    path = write_cases(tmp_path, [decline_case("same"), decline_case("same")])

    with pytest.raises(ValueError, match="duplicate case id"):
        load_cases(path)


def test_requires_gold_sql_for_sql_route(tmp_path):
    case = sql_case()
    del case["gold_sql"]

    with pytest.raises(ValueError, match="gold_sql"):
        load_cases(write_cases(tmp_path, [case]))


def test_rejects_sql_fields_for_non_sql_route(tmp_path):
    case = decline_case()
    case["gold_sql"] = "SELECT 1"

    with pytest.raises(ValueError, match="must not include gold_sql"):
        load_cases(write_cases(tmp_path, [case]))


@pytest.mark.parametrize("route", ["", "answer", 1])
def test_rejects_invalid_routes(tmp_path, route):
    case = decline_case()
    case["expected_route"] = route

    with pytest.raises(ValueError, match="expected_route"):
        load_cases(write_cases(tmp_path, [case]))


def test_rejects_invalid_comparison_mode(tmp_path):
    case = sql_case()
    case["comparison"]["mode"] = "same_rows"

    with pytest.raises(ValueError, match="comparison mode"):
        load_cases(write_cases(tmp_path, [case]))


def test_rejects_negative_float_tolerance(tmp_path):
    case = sql_case()
    case["comparison"]["float_tolerance"] = -0.1

    with pytest.raises(ValueError, match="float_tolerance"):
        load_cases(write_cases(tmp_path, [case]))


def test_rejects_unknown_fields(tmp_path):
    case = decline_case()
    case["difficulty"] = "easy"

    with pytest.raises(ValueError, match="unknown case fields"):
        load_cases(write_cases(tmp_path, [case]))


def test_rejects_empty_tags(tmp_path):
    case = decline_case()
    case["tags"] = []

    with pytest.raises(ValueError, match="tags"):
        load_cases(write_cases(tmp_path, [case]))
