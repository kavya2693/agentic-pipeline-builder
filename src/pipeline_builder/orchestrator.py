"""The run as an explicit state machine.

Each state is a function that does one job and returns an event; a transition table maps
(state, event) to the next state. Nothing calls the next step directly, so the order, the
stop conditions and the retry policy are all in the table and the log, not buried in a call
chain. docs/aws-mapping.md shows how the same table maps onto AWS Step Functions.
"""

from __future__ import annotations

import json
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd
import yaml

from pipeline_builder import builder, describe, validation
from pipeline_builder.dbt_run import DbtResult, run_dbt
from pipeline_builder.discovery import Catalog, discover, load_raw
from pipeline_builder.runtime import land_frame


@dataclass
class RunConfig:
    raw: Path
    out: Path
    as_of: date
    max_quarantine_rate: float = 0.05
    force: bool = False
    generate: describe.Generate | None = None
    run_dbt: bool = True
    # Folder holding a reviewed catalog.yml and rules.yml from an earlier run. When set, the
    # run enforces that contract instead of re-learning it from today's (possibly dirty) drop.
    contract: Path | None = None


@dataclass
class RunContext:
    cfg: RunConfig
    catalog: Catalog | None = None
    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    rules: list[validation.Rule] = field(default_factory=list)
    flags: pd.DataFrame | None = None
    summary: pd.DataFrame | None = None
    descriptions: describe.Descriptions | None = None
    plan: builder.BuildPlan | None = None
    dbt: DbtResult | None = None
    log: list[dict[str, Any]] = field(default_factory=list)

    @property
    def warehouse(self) -> Path:
        return self.cfg.out / "warehouse.duckdb"

    @property
    def dbt_dir(self) -> Path:
        return self.cfg.out / "dbt"


Handler = Callable[[RunContext], str]


def discover_state(ctx: RunContext) -> str:
    if ctx.cfg.contract is not None:
        ctx.catalog = Catalog.from_dict(yaml.safe_load((ctx.cfg.contract / "catalog.yml").read_text()))
        missing = [s.file for s in ctx.catalog.tables.values() if not (ctx.cfg.raw / s.file).exists()]
        if missing:
            return "empty"
        ctx.frames = {t: load_raw(ctx.cfg.raw / s.file) for t, s in ctx.catalog.tables.items()}
        for t, df in ctx.frames.items():
            ctx.catalog.tables[t].row_count = len(df)
    else:
        ctx.catalog, ctx.frames = discover(ctx.cfg.raw)
    ctx.catalog.write(ctx.cfg.out / "catalog.yml")
    return "ok" if ctx.catalog.tables else "empty"


def validate_state(ctx: RunContext) -> str:
    assert ctx.catalog is not None
    if ctx.cfg.contract is not None:
        ctx.rules = validation.read_rules(ctx.cfg.contract / "rules.yml")
    else:
        ctx.rules = validation.build_rules(ctx.catalog, ctx.frames, ctx.cfg.as_of)
    validation.write_rules(ctx.rules, ctx.cfg.out / "rules.yml")
    ctx.flags = validation.run_rules(ctx.rules, ctx.catalog, ctx.frames)
    ctx.summary = validation.summarise(ctx.flags, ctx.catalog)
    over = ctx.summary["quarantine_rate"] > ctx.cfg.max_quarantine_rate
    if over.any() and not ctx.cfg.force:
        return "too_many_rejects"
    return "ok"


def build_state(ctx: RunContext) -> str:
    assert ctx.catalog is not None
    ctx.descriptions = describe.describe(ctx.catalog, ctx.rules, ctx.cfg.generate)
    ctx.plan = builder.write_dbt_project(
        ctx.dbt_dir, ctx.catalog, ctx.rules, ctx.descriptions, ctx.warehouse.resolve()
    )
    builder.write_airflow_dag(
        ctx.cfg.out / "airflow" / "dags",
        ctx.catalog,
        ctx.cfg.raw.resolve(),
        ctx.warehouse.resolve(),
        ctx.dbt_dir.resolve(),
        (ctx.cfg.out / "rules.yml").resolve(),
        (ctx.cfg.out / "catalog.yml").resolve(),
        ctx.cfg.max_quarantine_rate,
    )
    return "ok"


def land_state(ctx: RunContext) -> str:
    ctx.warehouse.unlink(missing_ok=True)
    with duckdb.connect(str(ctx.warehouse)) as con:
        for t, df in ctx.frames.items():
            land_frame(con, t, df)
    return "ok" if ctx.cfg.run_dbt else "skip_dbt"


def dbt_state(ctx: RunContext) -> str:
    ctx.dbt = run_dbt(ctx.dbt_dir, ctx.warehouse)
    return "ok" if ctx.dbt.ok else "failed"


STATES: dict[str, Handler] = {
    "DISCOVER": discover_state,
    "VALIDATE": validate_state,
    "BUILD": build_state,
    "LAND": land_state,
    "DBT_BUILD": dbt_state,
}

TRANSITIONS: dict[tuple[str, str], str] = {
    ("DISCOVER", "ok"): "VALIDATE",
    ("DISCOVER", "empty"): "FAILED",
    ("VALIDATE", "ok"): "BUILD",
    ("VALIDATE", "too_many_rejects"): "HALTED",
    ("BUILD", "ok"): "LAND",
    ("LAND", "ok"): "DBT_BUILD",
    ("LAND", "skip_dbt"): "DONE",
    ("DBT_BUILD", "ok"): "DONE",
    ("DBT_BUILD", "failed"): "FAILED",
}
TERMINAL = {"DONE", "HALTED", "FAILED"}
# Only steps that touch an external process are worth retrying; a rule that fails on the
# data will fail the same way the second time.
MAX_ATTEMPTS = {"DBT_BUILD": 2, "LAND": 2}


def run(cfg: RunConfig) -> RunContext:
    cfg.out.mkdir(parents=True, exist_ok=True)
    ctx = RunContext(cfg)
    state = "DISCOVER"
    while state not in TERMINAL:
        attempts = MAX_ATTEMPTS.get(state, 1)
        event = "error"
        for attempt in range(1, attempts + 1):
            t0 = time.perf_counter()
            try:
                event = STATES[state](ctx)
                detail = ""
            except Exception as exc:
                event, detail = "error", "".join(traceback.format_exception_only(exc)).strip()
            ctx.log.append(
                {
                    "state": state,
                    "attempt": attempt,
                    "event": event,
                    "seconds": round(time.perf_counter() - t0, 2),
                    "detail": detail,
                }
            )
            if event != "error":
                break
        state = TRANSITIONS.get((state, event), "FAILED")
    ctx.log.append({"state": state, "attempt": 1, "event": "end", "seconds": 0.0, "detail": ""})
    (cfg.out / "run_state.json").write_text(json.dumps(ctx.log, indent=2))
    return ctx


def final_state(ctx: RunContext) -> str:
    return str(ctx.log[-1]["state"]) if ctx.log else "NOT_STARTED"
