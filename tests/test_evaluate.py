"""The scorer is tested against broken inputs before anything relies on its numbers."""

import pandas as pd

from pipeline_builder.evaluate import score_flags

DEFECTS = pd.DataFrame(
    [
        {"table": "orders", "row": 3, "column": "status", "defect": "invalid_enum"},
        {"table": "orders", "row": 9, "column": "*", "defect": "duplicate_row"},
    ]
)
COLS = ["table", "row", "column", "rule_id", "expectation", "severity"]


def flags(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=COLS)


def test_no_flags_means_zero_recall() -> None:
    r = score_flags(flags([]), DEFECTS)
    assert r["recall"] == 0.0
    assert r["flagged_rows"] == 0


def test_right_row_wrong_column_is_not_a_catch() -> None:
    r = score_flags(flags([("orders", 3, "channel", "x", "expect_column_values_to_be_in_set", "block")]), DEFECTS)
    assert r["by_defect"]["invalid_enum"]["caught"] == 0
    # The row was flagged, so it still counts toward precision; it is a defect row.
    assert r["row_precision"] == 1.0


def test_duplicate_needs_a_uniqueness_flag() -> None:
    not_unique = flags([("orders", 9, "status", "x", "expect_column_values_to_be_in_set", "block")])
    unique = flags([("orders", 9, "order_id", "u", "expect_column_values_to_be_unique", "block")])
    assert score_flags(not_unique, DEFECTS)["by_defect"]["duplicate_row"]["caught"] == 0
    assert score_flags(unique, DEFECTS)["by_defect"]["duplicate_row"]["caught"] == 1


def test_flagging_everything_buys_recall_but_not_precision() -> None:
    everything = flags(
        [("orders", i, c, "n", "expect_column_values_to_be_unique", "block") for i in range(100) for c in ("status",)]
    )
    r = score_flags(everything, DEFECTS)
    assert r["recall"] == 1.0
    assert r["row_precision"] == 0.02
