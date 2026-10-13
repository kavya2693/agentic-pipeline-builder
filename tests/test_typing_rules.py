import pandas as pd

from pipeline_builder import typing_rules as tr


def _ok(values: list[str], logical: str) -> list[bool]:
    return tr.parse(pd.Series(values), logical)[1].tolist()


def test_codes_are_not_numbers() -> None:
    # The two traps that pandas and naive regexes fall into.
    assert _ok(["SKU-000123", "01234"], "decimal") == [False, False]
    assert _ok(["01234", "007"], "integer") == [False, False]


def test_formatted_money_parses() -> None:
    parsed, ok = tr.parse(pd.Series(["$1,234.50", "AED 12.00", "-3.5", "12..50"]), "decimal")
    assert ok.tolist() == [True, True, True, False]
    assert parsed.iloc[0] == 1234.5


def test_mixed_date_formats_are_detected_and_parsed() -> None:
    s = pd.Series(["2024-03-01", "03/15/2024", "2024-04-02", "not a date"])
    fmts = tr.formats_used(s, tr.DATE_FORMATS)
    assert fmts[:2] == ["%Y-%m-%d", "%m/%d/%Y"]
    parsed, ok = tr.parse(s, "date", fmts)
    assert ok.tolist() == [True, True, True, False]
    assert parsed.iloc[1] == pd.Timestamp(2024, 3, 15)


def test_null_tokens_become_null() -> None:
    out = tr.normalise_nulls(pd.Series([" a ", "", "NULL", "n/a", None, "-"]))
    assert out.tolist() == ["a", None, None, None, None, None]
