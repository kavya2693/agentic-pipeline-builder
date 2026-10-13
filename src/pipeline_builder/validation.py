"""Validation agent: profile each table, write data-quality rules, and run them.

Rules are written as expectations in the Great Expectations vocabulary and saved as YAML,
so a reviewer can read, edit or delete one before the pipeline is built. Every rule carries
the profile evidence that produced it. Rules are derived from the data, which is also their
weakness: a defect common enough to look normal is written into the rule as normal. The
stress run in scripts/stress_defect_rate.py measures where that happens.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from pipeline_builder import typing_rules as tr
from pipeline_builder.discovery import Catalog, ColumnSchema, _key_text

# A column with at most this share of nulls is treated as required.
NOT_NULL_MAX = 0.01
# Categorical: few distinct values, each value kept must carry at least this share of rows.
ENUM_MAX_DISTINCT = 25
ENUM_MIN_SHARE = 0.001
ENUM_MIN_COVERAGE = 0.99
# Numbers that are almost never negative are required to be non-negative.
NON_NEGATIVE_SHARE = 0.99
# Upper outlier bound, as a multiple of the 99th percentile. Warn only: large is not wrong.
OUTLIER_FACTOR = 10.0
EARLIEST_DATE = date(1990, 1, 1)
PATTERN_MIN_SHARE = 0.99
PATTERN_MIN_DISTINCT = 50
EMAIL_RE = r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$"


@dataclass
class Rule:
    id: str
    table: str
    expectation: str
    columns: list[str]
    kwargs: dict[str, Any] = field(default_factory=dict)
    severity: str = "block"  # block: row is quarantined. warn: row is kept and reported.
    evidence: str = ""

    @property
    def column(self) -> str:
        return self.columns[0]


def write_rules(rules: list[Rule], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"rules": [asdict(r) for r in rules]}, sort_keys=False, width=110))


def read_rules(path: Path) -> list[Rule]:
    return [Rule(**r) for r in yaml.safe_load(path.read_text())["rules"]]


def _shape_regex(value: str) -> str:
    """Turn 'SKU-000123' into ^[A-Z]{3}-\\d{6}$ by run-length encoding character classes."""
    out: list[str] = []
    prev, run = "", 0

    def cls(ch: str) -> str:
        if ch.isdigit():
            return r"\d"
        if ch.isupper():
            return "[A-Z]"
        if ch.islower():
            return "[a-z]"
        return re.escape(ch)

    for ch in value:
        c = cls(ch)
        if c == prev:
            run += 1
            continue
        if prev:
            out.append(prev + (f"{{{run}}}" if run > 1 else ""))
        prev, run = c, 1
    if prev:
        out.append(prev + (f"{{{run}}}" if run > 1 else ""))
    return "^" + "".join(out) + "$"


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _looks_like_typo(value: str, share: pd.Series) -> bool:
    """A frequent value can still be a typo: one or two edits away from a value ten times as common.

    Frequency alone cannot separate a repeated typo ("shiped", 0.1% of rows) from a rare
    legitimate code ("disputed", 0.08%). Closeness to a dominant value can.
    """
    if not any(ch.isalnum() for ch in value):
        return True
    for other, s in share.items():
        if other != value and s >= 10 * share[value]:
            if _edit_distance(value, str(other)) <= max(1, len(str(other)) // 4):
                return True
    return False


def _rules_for_column(
    table: str, col: ColumnSchema, values: pd.Series, in_pk: bool, as_of: date, typo_check: bool = True
) -> list[Rule]:
    rules: list[Rule] = []
    rid = f"{table}__{col.name}"
    present = values.dropna()
    n = max(len(values), 1)

    if col.null_rate <= NOT_NULL_MAX or in_pk:
        rules.append(
            Rule(
                f"{rid}__not_null",
                table,
                "expect_column_values_to_not_be_null",
                [col.name],
                evidence=f"null rate {col.null_rate:.2%} (required at <= {NOT_NULL_MAX:.0%})",
            )
        )
    if present.empty:
        return rules

    if col.logical_type != "string":
        kw: dict[str, Any] = {"type": col.logical_type}
        if col.formats:
            kw["formats"] = col.formats
        rules.append(
            Rule(
                f"{rid}__type",
                table,
                "expect_column_values_to_be_of_type",
                [col.name],
                kw,
                evidence=f"{col.logical_type} inferred",
            )
        )
    parsed, ok = tr.parse(present, col.logical_type, col.formats)
    good = parsed[ok]

    if col.logical_type in ("integer", "decimal") and not col.is_identifier and not in_pk and len(good):
        nums = good.astype(float)
        if (nums >= 0).mean() >= NON_NEGATIVE_SHARE:
            rules.append(
                Rule(
                    f"{rid}__non_negative",
                    table,
                    "expect_column_values_to_be_between",
                    [col.name],
                    {"min_value": 0},
                    evidence=f"{(nums >= 0).mean():.2%} of values >= 0",
                )
            )
        q99 = float(nums.quantile(0.99))
        if q99 > 0:
            ceiling = round(q99 * OUTLIER_FACTOR, 4)
            rules.append(
                Rule(
                    f"{rid}__outlier",
                    table,
                    "expect_column_values_to_be_between",
                    [col.name],
                    {"max_value": ceiling},
                    severity="warn",
                    evidence=f"p99 {q99:g} x {OUTLIER_FACTOR:g}",
                )
            )

    if col.logical_type in ("date", "timestamp") and len(good):
        rules.append(
            Rule(
                f"{rid}__window",
                table,
                "expect_column_values_to_be_between",
                [col.name],
                {"min_value": EARLIEST_DATE.isoformat(), "max_value": as_of.isoformat()},
                evidence=f"dates must fall between {EARLIEST_DATE} and the run date {as_of}",
            )
        )

    if col.logical_type == "string" and not col.is_identifier:
        norm = present.astype(str).str.strip().str.lower()
        share = norm.value_counts(normalize=True)
        if len(share) <= ENUM_MAX_DISTINCT * 4:
            kept = share[share >= ENUM_MIN_SHARE]
            if typo_check:
                kept = kept[[not _looks_like_typo(v, kept) for v in kept.index]]
            if 0 < len(kept) <= ENUM_MAX_DISTINCT and kept.sum() >= ENUM_MIN_COVERAGE:
                rules.append(
                    Rule(
                        f"{rid}__in_set",
                        table,
                        "expect_column_values_to_be_in_set",
                        [col.name],
                        {"value_set": sorted(kept.index.tolist())},
                        evidence=f"{len(kept)} values cover {kept.sum():.2%}; rarer values excluded",
                    )
                )
                return rules

    if col.logical_type == "string":
        text = present.astype(str)
        if "email" in col.name.lower() or text.str.fullmatch(EMAIL_RE).mean() >= 0.95:
            rules.append(
                Rule(f"{rid}__pattern", table, "expect_column_values_to_match_regex", [col.name],
                     {"regex": EMAIL_RE}, evidence="email address")
            )
        elif text.nunique() >= PATTERN_MIN_DISTINCT:
            sample = text.sample(min(len(text), 20_000), random_state=0)
            shapes = sample.map(_shape_regex).value_counts(normalize=True)
            if shapes.iloc[0] >= PATTERN_MIN_SHARE:
                rules.append(
                    Rule(f"{rid}__pattern", table, "expect_column_values_to_match_regex", [col.name],
                         {"regex": shapes.index[0]}, evidence=f"one shape covers {shapes.iloc[0]:.2%}")
                )
    return rules


def build_rules(
    catalog: Catalog, frames: dict[str, pd.DataFrame], as_of: date, typo_check: bool = True
) -> list[Rule]:
    rules: list[Rule] = []
    for t, schema in catalog.tables.items():
        df = frames[t]
        if schema.primary_key:
            exp = (
                "expect_column_values_to_be_unique"
                if len(schema.primary_key) == 1
                else "expect_compound_columns_to_be_unique"
            )
            rules.append(
                Rule(f"{t}__pk__unique", t, exp, list(schema.primary_key), evidence="inferred primary key")
            )
        for col in schema.columns:
            rules.extend(
                _rules_for_column(t, col, df[col.name], col.name in schema.primary_key, as_of, typo_check)
            )
    for fk in catalog.foreign_keys:
        rules.append(
            Rule(
                f"{fk.table}__{fk.column}__references_{fk.ref_table}",
                fk.table,
                "expect_column_values_to_exist_in",
                [fk.column],
                {"ref_table": fk.ref_table, "ref_column": fk.ref_column},
                evidence=f"{fk.containment:.2%} of rows resolve to {fk.ref_table}.{fk.ref_column}",
            )
        )
    return rules


def naive_rules(catalog: Catalog) -> list[Rule]:
    """Baseline: what a hand-written first pass usually is. Every column required, first column unique."""
    rules: list[Rule] = []
    for t, schema in catalog.tables.items():
        first = schema.columns[0].name
        rules.append(Rule(f"{t}__{first}__unique", t, "expect_column_values_to_be_unique", [first]))
        for col in schema.columns:
            rules.append(Rule(f"{t}__{col.name}__not_null", t, "expect_column_values_to_not_be_null", [col.name]))
    return rules


def _evaluate(rule: Rule, df: pd.DataFrame, catalog: Catalog, frames: dict[str, pd.DataFrame]) -> pd.Series:
    """Boolean mask of rows that violate the rule."""
    exp = rule.expectation
    if exp == "expect_column_values_to_not_be_null":
        return df[rule.column].isna()
    if exp in ("expect_column_values_to_be_unique", "expect_compound_columns_to_be_unique"):
        keys = df[rule.columns]
        return keys.notna().all(axis=1) & keys.duplicated(keep="first")

    schema = catalog.tables[rule.table]
    col = schema.column(rule.column)
    values = df[rule.column]
    present = values.notna()
    parsed, ok = tr.parse(values.where(present, ""), col.logical_type, col.formats)
    ok = ok & present
    if exp == "expect_column_values_to_be_of_type":
        return present & ~ok
    if exp == "expect_column_values_to_be_in_set":
        norm = values.astype(str).str.strip().str.lower()
        return present & ~norm.isin(rule.kwargs["value_set"])
    if exp == "expect_column_values_to_match_regex":
        return present & ~values.astype(str).str.fullmatch(rule.kwargs["regex"]).fillna(False).astype(bool)
    if exp == "expect_column_values_to_be_between":
        lo, hi = rule.kwargs.get("min_value"), rule.kwargs.get("max_value")
        if col.logical_type in ("date", "timestamp"):
            v = pd.to_datetime(parsed)
            low = v < pd.Timestamp(lo) if lo is not None else False
            # A date equal to the run date is still valid, so the bound is the end of that day.
            high = v >= pd.Timestamp(hi) + pd.Timedelta(days=1) if hi is not None else False
        else:
            v = pd.to_numeric(parsed, errors="coerce")
            low = v < lo if lo is not None else False
            high = v > hi if hi is not None else False
        return ok & (pd.Series(low, index=df.index) | pd.Series(high, index=df.index)).fillna(False)
    if exp == "expect_column_values_to_exist_in":
        parent = catalog.tables[rule.kwargs["ref_table"]]
        ref = rule.kwargs["ref_column"]
        logical = parent.column(ref).logical_type
        keys = set(_key_text(frames[parent.name][ref], logical).dropna())
        child = _key_text(values, logical)
        return child.notna() & ~child.isin(keys)
    raise ValueError(f"unknown expectation {exp}")


def run_rules(rules: list[Rule], catalog: Catalog, frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """One row per (table, row, rule) violation. `row` is the 0-based position in the raw file."""
    parts = []
    for rule in rules:
        df = frames[rule.table]
        mask = _evaluate(rule, df, catalog, frames).to_numpy(dtype=bool)
        if not mask.any():
            continue
        rows = df.index.to_numpy()[mask]
        parts.append(
            pd.DataFrame(
                {
                    "table": rule.table,
                    "row": rows,
                    "column": "*" if len(rule.columns) > 1 else rule.column,
                    "rule_id": rule.id,
                    "expectation": rule.expectation,
                    "severity": rule.severity,
                }
            )
        )
    cols = ["table", "row", "column", "rule_id", "expectation", "severity"]
    return pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=cols)


def summarise(flags: pd.DataFrame, catalog: Catalog) -> pd.DataFrame:
    """Per-table rows, rows quarantined (any blocking rule) and rows warned."""
    out = []
    for t, schema in catalog.tables.items():
        f = flags[flags["table"] == t]
        blocked = f.loc[f["severity"] == "block", "row"].nunique()
        warned = f.loc[f["severity"] == "warn", "row"].nunique()
        out.append(
            {
                "table": t,
                "rows": schema.row_count,
                "quarantined": int(blocked),
                "quarantine_rate": round(blocked / max(schema.row_count, 1), 5),
                "warned": int(warned),
            }
        )
    return pd.DataFrame(out)
