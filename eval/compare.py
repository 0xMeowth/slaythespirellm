import hashlib
import json
import math

from eval.models import (
    ComparisonMode,
    ComparisonResult,
    ComparisonSpec,
    QueryResult,
    ResultSummary,
)


def compare_results(
    gold: QueryResult,
    predicted: QueryResult,
    spec: ComparisonSpec,
) -> ComparisonResult:
    if spec.mode == "scalar":
        return _compare_scalar(gold, predicted, spec.float_tolerance)

    if not _same_shape_check(gold, predicted):
        return ComparisonResult(False, "wrong_shape")

    if spec.mode == "ordered_rows":
        if _ordered_rows_equal_check(
            gold.rows,
            predicted.rows,
            spec.float_tolerance,
        ):
            return ComparisonResult(True, None)
        if _unordered_rows_equal_check(
            gold.rows,
            predicted.rows,
            spec.float_tolerance,
        ):
            return ComparisonResult(False, "wrong_order")
        return ComparisonResult(False, "result_mismatch")

    if _unordered_rows_equal_check(
        gold.rows,
        predicted.rows,
        spec.float_tolerance,
    ):
        return ComparisonResult(True, None)
    return ComparisonResult(False, "result_mismatch")


def summarize_result(
    result: QueryResult,
    mode: ComparisonMode,
    preview_limit: int,
) -> ResultSummary:
    if preview_limit < 0:
        raise ValueError("preview_limit must not be negative")

    encoded_rows = [_encode_row(row) for row in result.rows]
    if mode == "unordered_rows":
        encoded_rows.sort(key=_canonical_json)
    payload = {
        "column_count": result.column_count,
        "rows": encoded_rows,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode()).hexdigest()
    return ResultSummary(
        row_count=len(result.rows),
        sha256=digest,
        preview=result.rows[:preview_limit],
    )


def _compare_scalar(
    gold: QueryResult,
    predicted: QueryResult,
    tolerance: float,
) -> ComparisonResult:
    if (
        gold.column_count != 1
        or predicted.column_count != 1
        or len(gold.rows) != 1
        or len(predicted.rows) != 1
        or len(gold.rows[0]) != 1
        or len(predicted.rows[0]) != 1
    ):
        return ComparisonResult(False, "wrong_shape")
    if _values_equal(gold.rows[0][0], predicted.rows[0][0], tolerance):
        return ComparisonResult(True, None)
    return ComparisonResult(False, "result_mismatch")


def _same_shape_check(gold: QueryResult, predicted: QueryResult) -> bool:
    if gold.column_count != predicted.column_count:
        return False
    if len(gold.rows) != len(predicted.rows):
        return False
    return all(len(row) == gold.column_count for row in gold.rows + predicted.rows)


def _ordered_rows_equal_check(
    gold: tuple[tuple[object, ...], ...],
    predicted: tuple[tuple[object, ...], ...],
    tolerance: float,
) -> bool:
    return all(
        _row_equal(gold_row, predicted_row, tolerance)
        for gold_row, predicted_row in zip(gold, predicted, strict=True)
    )


def _unordered_rows_equal_check(
    gold: tuple[tuple[object, ...], ...],
    predicted: tuple[tuple[object, ...], ...],
    tolerance: float,
) -> bool:
    unmatched = list(predicted)
    for gold_row in gold:
        match = next(
            (
                index
                for index, predicted_row in enumerate(unmatched)
                if _row_equal(gold_row, predicted_row, tolerance)
            ),
            None,
        )
        if match is None:
            return False
        unmatched.pop(match)
    return not unmatched


def _row_equal(
    gold: tuple[object, ...],
    predicted: tuple[object, ...],
    tolerance: float,
) -> bool:
    return len(gold) == len(predicted) and all(
        _values_equal(gold_value, predicted_value, tolerance)
        for gold_value, predicted_value in zip(gold, predicted, strict=True)
    )


def _values_equal(gold: object, predicted: object, tolerance: float) -> bool:
    if type(gold) is not type(predicted):
        return False
    if isinstance(gold, float):
        return math.isclose(gold, predicted, rel_tol=0.0, abs_tol=tolerance)
    return gold == predicted


def _encode_row(row: tuple[object, ...]) -> list[list[object]]:
    return [_encode_value(value) for value in row]


def _encode_value(value: object) -> list[object]:
    if value is None:
        return ["null", None]
    if isinstance(value, bytes):
        return ["bytes", value.hex()]
    return [type(value).__name__, value]


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
