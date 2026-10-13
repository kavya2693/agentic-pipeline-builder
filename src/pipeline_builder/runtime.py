"""Task bodies shared by the CLI and the generated Airflow DAG.

The DAG imports these rather than carrying its own copies, so a scheduled run and a local
run land and validate data with the same code.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from pipeline_builder.discovery import Catalog, load_raw, table_name
from pipeline_builder.validation import read_rules, run_rules, summarise


class QuarantineThresholdExceeded(RuntimeError):
    """A table would lose more rows to quarantine than the run allows."""


def land_frame(con: duckdb.DuckDBPyConnection, table: str, df: pd.DataFrame) -> int:
    frame = df.copy()
    frame.insert(0, "_row_id", range(len(frame)))
    con.execute("create schema if not exists raw")
    con.register("landing", frame)
    cols = ", ".join(['cast(_row_id as bigint) as _row_id'] + [f'cast("{c}" as varchar) as "{c}"' for c in df.columns])
    con.execute(f"create or replace table raw.{table} as select {cols} from landing")
    con.unregister("landing")
    return len(frame)


def land_source(path: str, table: str | None = None, warehouse: str = "warehouse.duckdb") -> int:
    p = Path(path)
    with duckdb.connect(warehouse) as con:
        return land_frame(con, table or table_name(p), load_raw(p))


def validate_drop(
    raw_dir: str, catalog_path: str, rules_path: str, max_quarantine_rate: float = 0.05
) -> list[dict[str, Any]]:
    catalog = Catalog.from_dict(yaml.safe_load(Path(catalog_path).read_text()))
    rules = read_rules(Path(rules_path))
    frames = {t: load_raw(Path(raw_dir) / s.file) for t, s in catalog.tables.items()}
    summary = summarise(run_rules(rules, catalog, frames), catalog)
    over = summary[summary["quarantine_rate"] > max_quarantine_rate]
    if len(over):
        raise QuarantineThresholdExceeded(json.dumps(over.to_dict(orient="records")))
    return list(summary.to_dict(orient="records"))
