from eval.compare import compare_results, summarize_result
from eval.models import ComparisonSpec, QueryResult


def test_scalar_accepts_value_within_tolerance():
    gold = QueryResult(1, ((0.5,),))
    predicted = QueryResult(1, ((0.50001,),))

    result = compare_results(
        gold,
        predicted,
        ComparisonSpec("scalar", float_tolerance=0.0001),
    )

    assert result.passed
    assert result.failure_category is None


def test_scalar_requires_one_row_and_column():
    gold = QueryResult(1, ((1,),))
    predicted = QueryResult(1, ((1,), (1,)))

    result = compare_results(gold, predicted, ComparisonSpec("scalar"))

    assert not result.passed
    assert result.failure_category == "wrong_shape"


def test_ordered_rows_rejects_reversed_order():
    gold = QueryResult(1, (("a",), ("b",)))
    predicted = QueryResult(1, (("b",), ("a",)))

    result = compare_results(gold, predicted, ComparisonSpec("ordered_rows"))

    assert not result.passed
    assert result.failure_category == "wrong_order"


def test_unordered_rows_accepts_reversed_order():
    gold = QueryResult(1, (("a",), ("b",)))
    predicted = QueryResult(1, (("b",), ("a",)))

    result = compare_results(gold, predicted, ComparisonSpec("unordered_rows"))

    assert result.passed


def test_unordered_rows_preserves_duplicates():
    gold = QueryResult(1, (("a",), ("a",)))
    predicted = QueryResult(1, (("a",),))

    result = compare_results(gold, predicted, ComparisonSpec("unordered_rows"))

    assert not result.passed
    assert result.failure_category == "wrong_shape"


def test_rejects_wrong_column_count():
    gold = QueryResult(1, (("a",),))
    predicted = QueryResult(2, (("a", 1),))

    result = compare_results(gold, predicted, ComparisonSpec("ordered_rows"))

    assert not result.passed
    assert result.failure_category == "wrong_shape"


def test_empty_results_compare_equal():
    gold = QueryResult(1, ())
    predicted = QueryResult(1, ())

    result = compare_results(gold, predicted, ComparisonSpec("unordered_rows"))

    assert result.passed


def test_integer_and_float_are_not_equal():
    gold = QueryResult(1, ((1,),))
    predicted = QueryResult(1, ((1.0,),))

    result = compare_results(gold, predicted, ComparisonSpec("scalar"))

    assert not result.passed
    assert result.failure_category == "result_mismatch"


def test_summary_hash_is_deterministic_for_ordered_rows():
    result = QueryResult(1, (("a",), ("b",)))

    first = summarize_result(result, "ordered_rows", preview_limit=1)
    second = summarize_result(result, "ordered_rows", preview_limit=1)

    assert first == second
    assert first.row_count == 2
    assert first.preview == (("a",),)


def test_unordered_summary_hash_ignores_order():
    first = summarize_result(
        QueryResult(1, (("a",), ("b",))),
        "unordered_rows",
        preview_limit=2,
    )
    second = summarize_result(
        QueryResult(1, (("b",), ("a",))),
        "unordered_rows",
        preview_limit=2,
    )

    assert first.sha256 == second.sha256


def test_ordered_summary_hash_preserves_order():
    first = summarize_result(
        QueryResult(1, (("a",), ("b",))),
        "ordered_rows",
        preview_limit=2,
    )
    second = summarize_result(
        QueryResult(1, (("b",), ("a",))),
        "ordered_rows",
        preview_limit=2,
    )

    assert first.sha256 != second.sha256
