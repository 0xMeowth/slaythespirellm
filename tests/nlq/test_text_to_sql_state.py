import pytest

from nlq.text_to_sql_state import create_initial_state, create_run_result


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
