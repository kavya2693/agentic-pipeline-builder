"""Inline the exported contract sheet into compositions/value.html (renders cannot fetch files).

Usage: python3 tools/inject_contract.py assets/contract_sheet.json compositions/value.html \
         --rules orders__pk__unique,orders__customer_id__not_null,...
"""

import argparse
import json
import re

ap = argparse.ArgumentParser()
ap.add_argument("sheet")
ap.add_argument("html")
ap.add_argument("--rules", required=True, help="rule ids shown as sheet rows, in order")
a = ap.parse_args()

with open(a.sheet) as fh:
    d = json.load(fh)
by_id = {r["id"]: r for r in d["rules"]}
ids = a.rules.split(",")
shown = [by_id[i] for i in ids]
rest = [r for r in d["rules"] if r["id"] not in ids]


def short_check(r: dict) -> str:
    e, k = r["expectation"], r["kwargs"]
    if e == "expect_column_values_to_be_unique":
        return "unique"
    if e == "expect_column_values_to_not_be_null":
        return "not null"
    if e == "expect_column_values_to_be_of_type":
        return "is " + str(k["type"])
    if e == "expect_column_values_to_be_in_set":
        return f"one of {len(k['value_set'])} values"
    if e == "expect_column_values_to_exist_in":
        return "in " + k["ref_table"]
    if e == "expect_column_values_to_be_between":
        if isinstance(k.get("min_value"), str):
            return "date in window"
        return "range"
    return r["check"]


slim = {
    "table": d["table"],
    "file": d["file"],
    "rows": d["rows"],
    "quarantined": d["quarantined"],
    "warned": d["warned"],
    "rules_table": d["rules_table"],
    "more_rules": len(rest),
    "more_rules_holding": sum(r["rows_flagged"] > 0 for r in rest),
    "max_rate": max(s["quarantine_rate"] for s in d["summary"]),
    "tables": len(d["summary"]),
    "clean": d["rows"] - d["quarantined"],
    "rows_shown": [
        {
            "id": r["id"],
            "column": r["column"],
            "check": short_check(r),
            "severity": r["severity"],
            "evidence": r["evidence"],
            "held": r["rows_flagged"],
            "false_alarms": r["false_alarms"],
            "value_set": r["kwargs"].get("value_set"),
        }
        for r in shown
    ],
}
with open(a.html) as fh:
    html = fh.read()
block = "/* SHEET:BEGIN */ const SHEET = " + json.dumps(slim, separators=(",", ":")) + "; /* SHEET:END */"
html, n = re.subn(r"/\* SHEET:BEGIN \*/.*?/\* SHEET:END \*/", lambda _: block, html, flags=re.S)
assert n == 1, "marker not found"
with open(a.html, "w") as fh:
    fh.write(html)
print("injected", len(slim["rows_shown"]), "rules;", slim["more_rules"], "more")
