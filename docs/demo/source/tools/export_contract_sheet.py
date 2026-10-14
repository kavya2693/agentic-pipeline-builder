"""Re-run the repo's own discovery and validation agents and export the orders contract sheet.

Same drop (data/raw from `make data`, seeded), run date (AS_OF 2026-02-01) and thresholds as
`make run`. For one table (orders) it lists every rule the validation agent wrote, with its
evidence, severity and how many rows it holds back, plus the per-table quarantine summary for
the whole drop. Rows flagged with no planted defect come from the answer key in
data/ground_truth (the same scoring as evaluate.false_alarms_by_rule).

Run:  cd <repo> && PYTHONPATH=src .venv/bin/python <this file> <out.json>
"""

import json
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from pipeline_builder import validation
from pipeline_builder.discovery import discover

TABLE = "orders"
AS_OF = date(2026, 2, 1)

catalog, frames = discover(Path("data/raw"))
rules = validation.build_rules(catalog, frames, AS_OF)
flags = validation.run_rules(rules, catalog, frames)
summary = validation.summarise(flags, catalog)
defects = pd.read_csv("data/ground_truth/defects.csv")
defect_rows = set(zip(defects["table"], defects["row"].astype(int), strict=True))


def check_text(r: validation.Rule) -> str:
    k = r.kwargs
    e = r.expectation
    if e == "expect_column_values_to_be_unique":
        return "unique"
    if e == "expect_column_values_to_not_be_null":
        return "not null"
    if e == "expect_column_values_to_be_of_type":
        return "type " + str(k.get("type"))
    if e == "expect_column_values_to_be_in_set":
        return "in set (" + str(len(k.get("value_set", []))) + " values)"
    if e == "expect_column_values_to_be_between":
        lo, hi = k.get("min_value"), k.get("max_value")
        if lo is not None and hi is not None:
            return f"between {lo} and {hi}"
        return f">= {lo}" if lo is not None else f"<= {hi}"
    if e == "expect_column_values_to_exist_in":
        return "exists in " + k["ref_table"]
    if e == "expect_column_values_to_match_regex":
        return "matches pattern"
    return e


out_rules = []
for r in rules:
    if r.table != TABLE:
        continue
    f = flags[flags["rule_id"] == r.id]
    rows = set(int(x) for x in f["row"])
    false_alarms = sum((TABLE, x) not in defect_rows for x in rows)
    out_rules.append(
        {
            "id": r.id,
            "column": r.column if len(r.columns) == 1 else "+".join(r.columns),
            "expectation": r.expectation,
            "check": check_text(r),
            "kwargs": r.kwargs,
            "severity": r.severity,
            "evidence": r.evidence,
            "rows_flagged": len(rows),
            "false_alarms": false_alarms,
        }
    )

s = summary.set_index("table")
out = {
    "table": TABLE,
    "file": catalog.tables[TABLE].file,
    "as_of": AS_OF.isoformat(),
    "rows": int(s.at[TABLE, "rows"]),
    "quarantined": int(s.at[TABLE, "quarantined"]),
    "warned": int(s.at[TABLE, "warned"]),
    "rules_total": len(rules),
    "rules_table": len(out_rules),
    "rules": out_rules,
    "summary": summary.to_dict(orient="records"),
}
Path(sys.argv[1]).write_text(json.dumps(out, indent=1, default=str))
print({k: v for k, v in out.items() if k not in ("rules", "summary")})
print(summary.to_string(index=False))
