"""Score discovery and validation against the generator's answer key.

Two baselines sit next to the agents. For types: what pandas and DuckDB guess when a file
is read with their defaults. For data quality: a hand-written first pass that marks every
column required and the first column unique. Recall and precision are both reported because
a rule set can buy recall by flagging everything.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from pipeline_builder.discovery import Catalog, table_name


def load_truth(path: Path) -> tuple[dict[str, Any], pd.DataFrame]:
    schema = json.loads((path / "schema.json").read_text())
    defects = pd.read_csv(path / "defects.csv")
    return schema, defects


def _pandas_logical(dtype: Any, values: pd.Series) -> str:
    kind = str(dtype)
    if kind == "bool":
        return "boolean"
    if kind.startswith("int"):
        return "integer"
    if kind.startswith("float"):
        # pandas widens integer columns that contain nulls to float; it cannot tell them apart.
        return "decimal"
    if kind.startswith("datetime"):
        return "timestamp"
    return "string"


def pandas_default_types(raw: Path) -> dict[str, dict[str, str]]:
    """Types pandas assigns with default readers (no dtype hints, no date parsing)."""
    out: dict[str, dict[str, str]] = {}
    for p in sorted(raw.iterdir()):
        t = table_name(p)
        if p.suffix == ".csv":
            df = pd.read_csv(p, low_memory=False)
        elif p.suffix == ".parquet":
            df = pd.read_parquet(p)
        elif p.suffix == ".jsonl":
            df = pd.json_normalize([json.loads(x) for x in p.read_text().splitlines() if x.strip()], sep="_")
        elif p.suffix == ".json":
            df = pd.json_normalize(json.loads(p.read_text()), sep="_")
        else:
            continue
        out[t] = {c: _pandas_logical(df[c].dtype, df[c]) for c in df.columns}
    return out


def _duck_logical(type_name: str) -> str:
    t = type_name.upper()
    if t == "BOOLEAN":
        return "boolean"
    if t in {"TINYINT", "SMALLINT", "INTEGER", "BIGINT", "HUGEINT", "UBIGINT", "UINTEGER"}:
        return "integer"
    if t in {"DOUBLE", "FLOAT", "REAL"} or t.startswith("DECIMAL"):
        return "decimal"
    if t == "DATE":
        return "date"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    return "string"


def duckdb_sniffed_types(raw: Path) -> dict[str, dict[str, str]]:
    """Types DuckDB's auto-detecting readers assign. Nested JSON structs are expanded to child fields."""
    con = duckdb.connect()
    out: dict[str, dict[str, str]] = {}
    for p in sorted(raw.iterdir()):
        reader = {
            ".csv": "read_csv_auto",
            ".parquet": "read_parquet",
            ".json": "read_json_auto",
            ".jsonl": "read_json_auto",
        }.get(p.suffix)
        if not reader:
            continue
        rel = con.sql(f"select * from {reader}('{p}')")
        cols: dict[str, str] = {}
        for name, typ in zip(rel.columns, rel.types, strict=True):
            if typ.id == "struct":
                for child_name, child_type in typ.children:
                    cols[f"{name}_{child_name}"] = _duck_logical(str(child_type))
            else:
                cols[name] = _duck_logical(str(typ))
        out[table_name(p)] = cols
    return out


def type_accuracy(guess: dict[str, dict[str, str]], truth: dict[str, Any]) -> dict[str, Any]:
    total = correct = 0
    wrong: list[str] = []
    for t, spec in truth["tables"].items():
        for col, logical in spec["columns"].items():
            total += 1
            got = guess.get(t, {}).get(col)
            if got == logical:
                correct += 1
            else:
                wrong.append(f"{t}.{col}: {got} (true {logical})")
    return {"correct": correct, "total": total, "accuracy": round(correct / total, 4), "wrong": wrong}


def score_schema(catalog: Catalog, truth: dict[str, Any], raw: Path) -> dict[str, Any]:
    agent_types = {t: {c.name: c.logical_type for c in s.columns} for t, s in catalog.tables.items()}
    pk_hits = [
        t
        for t, spec in truth["tables"].items()
        if t in catalog.tables and catalog.tables[t].primary_key == spec["primary_key"]
    ]
    true_fk = {(f["table"], f["column"], f["ref_table"], f["ref_column"]) for f in truth["foreign_keys"]}
    found_fk = {(f.table, f.column, f.ref_table, f.ref_column) for f in catalog.foreign_keys}
    tp = len(true_fk & found_fk)
    return {
        "types": {
            "agent": type_accuracy(agent_types, truth),
            "pandas_default": type_accuracy(pandas_default_types(raw), truth),
            "duckdb_sniff": type_accuracy(duckdb_sniffed_types(raw), truth),
        },
        "primary_keys": {"correct": len(pk_hits), "total": len(truth["tables"])},
        "foreign_keys": {
            "found": len(found_fk),
            "true": len(true_fk),
            "correct": tp,
            "precision": round(tp / max(len(found_fk), 1), 4),
            "recall": round(tp / max(len(true_fk), 1), 4),
            "missed": sorted(".".join(k[:2]) for k in true_fk - found_fk),
            "spurious": sorted(".".join(k[:2]) for k in found_fk - true_fk),
        },
    }


def score_flags(flags: pd.DataFrame, defects: pd.DataFrame) -> dict[str, Any]:
    """Defect recall (by type) and row-level precision of a set of rule violations.

    A defect counts as caught when a rule flags its row on its column. A duplicate row has
    no single column, so any uniqueness violation on that row catches it.
    """
    f = flags.copy()
    f["row"] = f["row"].astype(int)
    on_cell = set(zip(f["table"], f["row"], f["column"], strict=True))
    uniq = f[f["expectation"].str.contains("unique")]
    on_dup = set(zip(uniq["table"], uniq["row"], strict=True))

    def caught(d: pd.Series) -> bool:
        if d["defect"] == "duplicate_row":
            return (d["table"], int(d["row"])) in on_dup
        return (d["table"], int(d["row"]), d["column"]) in on_cell

    d = defects.copy()
    d["caught"] = d.apply(caught, axis=1) if len(d) else []
    by_type = (
        d.groupby("defect")["caught"]
        .agg(["sum", "count"])
        .rename(columns={"sum": "caught", "count": "injected"})
    )
    by_type["recall"] = (by_type["caught"] / by_type["injected"]).round(4)

    defect_rows = set(zip(d["table"], d["row"].astype(int), strict=True))
    flagged_rows = set(zip(f["table"], f["row"], strict=True))
    true_rows = len(flagged_rows & defect_rows)
    return {
        "injected": len(d),
        "caught": int(d["caught"].sum()),
        "recall": round(float(d["caught"].mean()), 4) if len(d) else 0.0,
        "flagged_rows": len(flagged_rows),
        "flagged_defect_rows": true_rows,
        "row_precision": round(true_rows / max(len(flagged_rows), 1), 4),
        "by_defect": {
            k: {"injected": int(v["injected"]), "caught": int(v["caught"]), "recall": float(v["recall"])}
            for k, v in by_type.iterrows()
        },
    }


def false_alarms_by_rule(flags: pd.DataFrame, defects: pd.DataFrame) -> dict[str, int]:
    """Rows a rule flagged that carry no injected defect, per rule. These are the cost side."""
    defect_rows = set(zip(defects["table"], defects["row"].astype(int), strict=True))
    clean = [(t, int(r)) not in defect_rows for t, r in zip(flags["table"], flags["row"], strict=True)]
    counts = flags.loc[clean].drop_duplicates(["table", "row", "rule_id"])["rule_id"].value_counts()
    return {str(k): int(v) for k, v in counts.items()}
