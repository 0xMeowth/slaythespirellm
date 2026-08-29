import pytest

from nlq.text_to_sql_model_output import (
    ModelOutputError,
    parse_generated_sql,
    parse_route_response,
)


def test_parses_sql_route():
    decision = parse_route_response(
        '{"route":"sql","reason":"answerable from run data"}'
    )

    assert decision.route == "sql"
    assert decision.reason == "answerable from run data"


def test_parses_decline_route():
    decision = parse_route_response(
        '{"route":"decline","reason":"strategy advice"}'
    )

    assert decision.route == "decline"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "{}",
        '{"route":"maybe","reason":"unknown"}',
        '{"route":"sql","reason":" "}',
        '{"route":"sql","reason":"ok","extra":true}',
    ],
)
def test_rejects_malformed_route_response(text):
    with pytest.raises(ModelOutputError) as raised:
        parse_route_response(text)

    assert raised.value.category == "router_output_error"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("SELECT COUNT(*) FROM runs", "SELECT COUNT(*) FROM runs"),
        (
            "```sql\nSELECT COUNT(*) FROM runs\n```",
            "SELECT COUNT(*) FROM runs",
        ),
        (
            "```SQL\nSELECT COUNT(*) FROM runs\n```",
            "SELECT COUNT(*) FROM runs",
        ),
        (
            "WITH x AS (SELECT 1) SELECT * FROM x",
            "WITH x AS (SELECT 1) SELECT * FROM x",
        ),
    ],
)
def test_parses_generated_sql(text, expected):
    assert parse_generated_sql(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Here is the SQL: SELECT COUNT(*) FROM runs",
        "```python\nprint('x')\n```",
        "```sql\nSELECT 1\n```\n```sql\nSELECT 2\n```",
        "DELETE FROM runs",
    ],
)
def test_rejects_non_sql_model_output(text):
    with pytest.raises(ModelOutputError) as raised:
        parse_generated_sql(text)

    assert raised.value.category == "model_output_error"
