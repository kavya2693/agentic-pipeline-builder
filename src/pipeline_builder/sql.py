"""DuckDB SQL equivalents of the parsing and rule logic in typing_rules and validation.

Each function here mirrors one pandas function. The E2E run compares the rows the dbt
quarantine rejects with the rows the pandas runner flags, so a drift between the two
implementations shows up as a disagreement count rather than as a silent difference.
"""

from __future__ import annotations

from pipeline_builder import typing_rules as tr
from pipeline_builder.discovery import ColumnSchema
from pipeline_builder.validation import Rule


def lit(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def text_col(name: str) -> str:
    return f"{name}__text"


def parse_expr(col: ColumnSchema) -> str:
    t = text_col(col.name)
    if col.logical_type == "boolean":
        trues = ", ".join(lit(x) for x in sorted(tr.TRUE_TOKENS))
        falses = ", ".join(lit(x) for x in sorted(tr.FALSE_TOKENS))
        return f"case when lower({t}) in ({trues}) then true when lower({t}) in ({falses}) then false end"
    if col.logical_type == "integer":
        return (
            f"case when regexp_full_match({t}, {lit(tr.INTEGER_RE)}) "
            f"then try_cast(replace({t}, ',', '') as bigint) end"
        )
    if col.logical_type == "decimal":
        strip = lit(f"^{tr.CURRENCY_PREFIX}|,")
        return (
            f"case when regexp_full_match({t}, {lit(tr.DECIMAL_RE)}) "
            f"then try_cast(regexp_replace({t}, {strip}, '', 'g') as decimal(18, 4)) end"
        )
    if col.logical_type in ("date", "timestamp"):
        fmts = col.formats or list(tr.DATE_FORMATS if col.logical_type == "date" else tr.TIMESTAMP_FORMATS)
        fmt_list = "[" + ", ".join(lit(f) for f in fmts) + "]"
        cast = "date" if col.logical_type == "date" else "timestamp"
        return f"cast(try_strptime({t}, {fmt_list}) as {cast})"
    return t


def output_expr(col: ColumnSchema, categorical: bool) -> str:
    """The staging value: parsed, and categorical text standardised to lower case."""
    if col.logical_type == "string" and categorical:
        return f"lower({text_col(col.name)})"
    return parse_expr(col)


def violation_expr(rule: Rule, parent_alias: str | None = None) -> str:
    """SQL boolean that is true when the row violates the rule (null-safe: never null)."""
    exp, kw = rule.expectation, rule.kwargs
    c = rule.column
    t = text_col(c)
    if exp == "expect_column_values_to_not_be_null":
        return f"{t} is null"
    if exp in ("expect_column_values_to_be_unique", "expect_compound_columns_to_be_unique"):
        return "_key_seq > 1"
    if exp == "expect_column_values_to_be_of_type":
        return f"({t} is not null and {c} is null)"
    if exp == "expect_column_values_to_be_in_set":
        values = ", ".join(lit(v) for v in kw["value_set"])
        return f"({t} is not null and lower({t}) not in ({values}))"
    if exp == "expect_column_values_to_match_regex":
        return f"({t} is not null and not regexp_full_match({t}, {lit(kw['regex'])}))"
    if exp == "expect_column_values_to_be_between":
        parts = []
        is_date = isinstance(kw.get("min_value"), str) or isinstance(kw.get("max_value"), str)
        if kw.get("min_value") is not None:
            lo = f"timestamp {lit(kw['min_value'])}" if is_date else str(kw["min_value"])
            parts.append(f"{c} < {lo}")
        if kw.get("max_value") is not None:
            if is_date:
                parts.append(f"{c} >= timestamp {lit(kw['max_value'])} + interval 1 day")
            else:
                parts.append(f"{c} > {kw['max_value']}")
        return f"coalesce({' or '.join(parts)}, false)"
    if exp == "expect_column_values_to_exist_in":
        return f"({c} is not null and {parent_alias}.k is null)"
    raise ValueError(f"no SQL for {exp}")
