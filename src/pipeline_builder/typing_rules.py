"""Logical types, the text forms each one accepts, and how to parse them.

Discovery, the pandas checks and the generated dbt SQL all read raw values through the
definitions in this module, so a value that one of them calls "an integer" is an integer
for the other two as well. Keeping the three in step is what makes the pandas flags and the
dbt quarantine comparable row by row.
"""

from __future__ import annotations

from typing import Literal

import pandas as pd

LogicalType = Literal["boolean", "integer", "decimal", "date", "timestamp", "string"]
LOGICAL_TYPES: tuple[LogicalType, ...] = ("boolean", "integer", "decimal", "date", "timestamp", "string")

# Tokens that exporters write for "no value". Treated as null everywhere, never as a defect.
NULL_TOKENS = frozenset({"", "null", "none", "nan", "n/a", "na", "nil", "-"})

TRUE_TOKENS = frozenset({"true", "t", "yes", "y", "1"})
FALSE_TOKENS = frozenset({"false", "f", "no", "n", "0"})

# A leading zero on a multi-digit value means a code (postal code, account number), not a number.
INTEGER_RE = r"^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$"
# Currency symbols or codes and thousands separators are formatting, not part of the value.
# Codes are listed rather than matched as any three capitals, or "SKU-000123" reads as a
# negative amount in a currency called SKU.
CURRENCY_PREFIX = r"(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)"
DECIMAL_RE = rf"^{CURRENCY_PREFIX}?[+-]?(?:0|[1-9]\d*|[1-9]\d{{0,2}}(?:,\d{{3}})+)(?:\.\d+)?$"

DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%d %b %Y", "%Y/%m/%d")
TIMESTAMP_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%m/%d/%Y %H:%M",
    "%Y-%m-%dT%H:%M:%SZ",
)


def normalise_nulls(values: pd.Series) -> pd.Series:
    """Strip whitespace and turn exporter null tokens into real nulls. Output dtype is object."""
    s = values.astype("object")
    stripped = s.where(s.isna(), s.astype(str).str.strip())
    is_null_token = stripped.astype(str).str.lower().isin(NULL_TOKENS)
    return stripped.mask(is_null_token | stripped.isna(), None)


def _matches(values: pd.Series, pattern: str) -> pd.Series:
    return values.astype(str).str.fullmatch(pattern).fillna(False).astype(bool)


def parse_boolean(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    low = values.astype(str).str.lower()
    parsed = pd.Series(pd.NA, index=values.index, dtype="boolean")
    parsed[low.isin(TRUE_TOKENS)] = True
    parsed[low.isin(FALSE_TOKENS)] = False
    return parsed, parsed.notna().astype(bool)


def parse_integer(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    ok = _matches(values, INTEGER_RE)
    cleaned = values.astype(str).str.replace(",", "", regex=False)
    parsed = pd.to_numeric(cleaned.where(ok), errors="coerce").astype("Int64")
    return parsed, ok & parsed.notna()


def parse_decimal(values: pd.Series) -> tuple[pd.Series, pd.Series]:
    ok = _matches(values, DECIMAL_RE)
    cleaned = values.astype(str).str.replace(rf"^{CURRENCY_PREFIX}|,", "", regex=True)
    parsed = pd.to_numeric(cleaned.where(ok), errors="coerce")
    return parsed, ok & parsed.notna()


def parse_datetime(values: pd.Series, formats: tuple[str, ...] | list[str]) -> tuple[pd.Series, pd.Series]:
    parsed = pd.Series(pd.NaT, index=values.index, dtype="datetime64[ns]")
    text = values.astype(str)
    for fmt in formats:
        missing = parsed.isna()
        if not missing.any():
            break
        parsed[missing] = pd.to_datetime(text[missing], format=fmt, errors="coerce")
    return parsed, parsed.notna()


def formats_used(values: pd.Series, formats: tuple[str, ...], min_share: float = 0.001) -> list[str]:
    """Formats that actually occur in the column, most common first.

    A format is kept only if it explains a meaningful share of values, so a single corrupt
    value that happens to parse under an unusual format does not widen the accepted set.
    """
    text = values.astype(str)
    remaining = pd.Series(True, index=values.index)
    counts: dict[str, int] = {}
    for fmt in formats:
        hit = remaining & pd.to_datetime(text, format=fmt, errors="coerce").notna()
        counts[fmt] = int(hit.sum())
        remaining &= ~hit
    total = max(len(values), 1)
    kept = [f for f, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c / total >= min_share]
    return kept


def parse(values: pd.Series, logical: str, formats: list[str] | None = None) -> tuple[pd.Series, pd.Series]:
    """Parse already null-normalised text. Returns (parsed values, mask of values that parsed)."""
    if logical == "boolean":
        return parse_boolean(values)
    if logical == "integer":
        return parse_integer(values)
    if logical == "decimal":
        return parse_decimal(values)
    if logical == "date":
        return parse_datetime(values, formats or list(DATE_FORMATS))
    if logical == "timestamp":
        return parse_datetime(values, formats or list(TIMESTAMP_FORMATS))
    return values, values.notna()
