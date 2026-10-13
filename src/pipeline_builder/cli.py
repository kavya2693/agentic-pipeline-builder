"""pipeline-builder run: raw folder in, catalog + rules + dbt project + Airflow DAG out, then dbt build."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from pipeline_builder import evaluate, validation
from pipeline_builder.describe import ollama_client
from pipeline_builder.discovery import _singular
from pipeline_builder.orchestrator import RunConfig, RunContext, final_state, run


def sql_vs_pandas(ctx: RunContext) -> dict[str, Any]:
    """Rows the dbt quarantine rejected vs rows the pandas runner blocked, per table."""
    assert ctx.catalog is not None and ctx.flags is not None
    blocked = ctx.flags[ctx.flags["severity"] == "block"]
    out: dict[str, Any] = {}
    with duckdb.connect(str(ctx.warehouse.resolve())) as con:
        for t in ctx.catalog.tables:
            sql_rows = {
                int(r[0]) for r in con.execute(f"select _row_id from main.stg_{t}__rejected").fetchall()
            }
            pd_rows = set(blocked.loc[blocked["table"] == t, "row"].astype(int))
            out[t] = {
                "sql": len(sql_rows),
                "pandas": len(pd_rows),
                "both": len(sql_rows & pd_rows),
                "only_sql": len(sql_rows - pd_rows),
                "only_pandas": len(pd_rows - sql_rows),
            }
    return out


def mart_counts(ctx: RunContext) -> dict[str, int]:
    assert ctx.plan is not None
    names = (
        [f"dim_{_singular(t)}" for t in ctx.plan.dims]
        + [f"fct_{t}" for t in ctx.plan.facts]
        + [f"agg_{t}_daily" for t in ctx.plan.daily]
    )
    with duckdb.connect(str(ctx.warehouse.resolve())) as con:
        return {n: int(con.execute(f"select count(*) from main.{n}").fetchone()[0]) for n in names}  # type: ignore[index]


def generated_size(out: Path) -> dict[str, int]:
    files = [p for p in (out / "dbt").rglob("*") if p.is_file() and p.suffix in {".sql", ".yml"}]
    files = [p for p in files if "target" not in p.parts and "logs" not in p.parts]
    dag = list((out / "airflow").rglob("*.py"))
    return {
        "dbt_files": len(files),
        "dbt_lines": sum(len(p.read_text().splitlines()) for p in files),
        "dag_lines": sum(len(p.read_text().splitlines()) for p in dag),
        "rules": 0,
    }


def score(ctx: RunContext, truth_dir: Path) -> dict[str, Any]:
    assert ctx.catalog is not None and ctx.flags is not None
    truth, defects = evaluate.load_truth(truth_dir)
    schema = evaluate.score_schema(ctx.catalog, truth, ctx.cfg.raw)
    agent = evaluate.score_flags(ctx.flags, defects)
    naive = evaluate.score_flags(
        validation.run_rules(validation.naive_rules(ctx.catalog), ctx.catalog, ctx.frames), defects
    )
    freq_only_rules = validation.build_rules(ctx.catalog, ctx.frames, ctx.cfg.as_of, typo_check=False)
    freq_only = evaluate.score_flags(validation.run_rules(freq_only_rules, ctx.catalog, ctx.frames), defects)
    return {
        "schema": schema,
        "dq": {"agent": agent, "naive_baseline": naive, "agent_without_typo_check": freq_only},
    }


def write_report(metrics: dict[str, Any], path: Path) -> None:
    lines = ["# Run report", "", f"Final state: **{metrics['final_state']}**", ""]
    lines += ["## State log", "", "| state | attempt | event | seconds |", "|---|---|---|---|"]
    lines += [
        f"| {e['state']} | {e['attempt']} | {e['event']} | {e['seconds']} |" for e in metrics["state_log"]
    ]
    if "schema" in metrics:
        s = metrics["schema"]
        lines += [
            "",
            "## Schema inference vs answer key",
            "",
            "| method | columns typed correctly |",
            "|---|---|",
        ]
        for k, v in s["types"].items():
            lines.append(f"| {k} | {v['correct']}/{v['total']} ({v['accuracy']:.1%}) |")
        pk, fk = s["primary_keys"], s["foreign_keys"]
        lines += [
            "",
            f"Primary keys: {pk['correct']}/{pk['total']}. Foreign keys: {fk['correct']}/{fk['true']} found, "
            f"{fk['found'] - fk['correct']} spurious "
            f"(precision {fk['precision']:.0%}, recall {fk['recall']:.0%}).",
        ]
        lines += ["", "## Defects caught vs injected", ""]
        lines += ["| rule set | recall | flagged rows | row precision |", "|---|---|---|---|"]
        for k, v in metrics["dq"].items():
            lines.append(f"| {k} | {v['recall']:.1%} | {v['flagged_rows']:,} | {v['row_precision']:.1%} |")
        lines += ["", "| defect | injected | agent caught | recall |", "|---|---|---|---|"]
        for k, v in metrics["dq"]["agent"]["by_defect"].items():
            lines.append(f"| {k} | {v['injected']:,} | {v['caught']:,} | {v['recall']:.1%} |")
    if "dbt" in metrics:
        d = metrics["dbt"]
        lines += [
            "",
            "## dbt build",
            "",
            f"Models: {d['models']}. Tests: {d['tests']}. Elapsed {d['seconds']} s.",
            "",
            f"Warned tests (failing rows): {d['warned_tests']}",
            "",
            "| table | rejected by dbt SQL | blocked by pandas | both | only SQL | only pandas |",
            "|---|---|---|---|---|---|",
        ]
        for t, v in metrics.get("sql_vs_pandas", {}).items():
            lines.append(
                f"| {t} | {v['sql']:,} | {v['pandas']:,} | {v['both']:,} "
                f"| {v['only_sql']} | {v['only_pandas']} |"
            )
        lines += [
            "",
            "Mart row counts: " + ", ".join(f"{k} {v:,}" for k, v in metrics.get("marts", {}).items()),
        ]
    path.write_text("\n".join(lines) + "\n")


def cmd_run(args: argparse.Namespace) -> int:
    generate = ollama_client(args.llm) if args.llm else None
    cfg = RunConfig(
        raw=args.raw,
        out=args.out,
        as_of=date.fromisoformat(args.as_of) if args.as_of else date.today(),
        max_quarantine_rate=args.max_quarantine_rate,
        force=args.force,
        generate=generate,
        run_dbt=not args.no_dbt,
    )
    ctx = run(cfg)
    state = final_state(ctx)
    metrics: dict[str, Any] = {"final_state": state, "as_of": cfg.as_of.isoformat(), "state_log": ctx.log}
    if ctx.summary is not None:
        metrics["quarantine"] = ctx.summary.to_dict(orient="records")
        metrics["rules"] = len(ctx.rules)
    if ctx.descriptions is not None:
        metrics["descriptions"] = {
            "model_leads": ctx.descriptions.model_leads,
            "model_rejected": ctx.descriptions.model_rejected,
        }
    if ctx.dbt is not None:
        metrics["dbt"] = {
            "ok": ctx.dbt.ok,
            "models": dict(ctx.dbt.models),
            "tests": dict(ctx.dbt.tests),
            "warned_tests": ctx.dbt.warned_tests,
            "failed": ctx.dbt.failed,
            "seconds": ctx.dbt.seconds,
        }
        if ctx.dbt.ok:
            metrics["sql_vs_pandas"] = sql_vs_pandas(ctx)
            metrics["marts"] = mart_counts(ctx)
    if (cfg.out / "dbt").exists():
        size = generated_size(cfg.out)
        size["rules"] = len(ctx.rules)
        metrics["generated"] = size
    metrics["seconds_raw_to_built"] = round(sum(e["seconds"] for e in ctx.log), 1)
    if args.truth and ctx.flags is not None:
        metrics.update(score(ctx, args.truth))

    args.report.mkdir(parents=True, exist_ok=True)
    (args.report / "metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    write_report(metrics, args.report / "report.md")
    if ctx.summary is not None:
        print(ctx.summary.to_string(index=False))
    print((args.report / "report.md").read_text())
    return 0 if state == "DONE" else 1


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="pipeline-builder", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="discover, validate, build and dbt build one raw folder")
    r.add_argument("--raw", type=Path, required=True)
    r.add_argument("--out", type=Path, default=Path("generated"))
    r.add_argument("--report", type=Path, default=Path("outputs"))
    r.add_argument("--truth", type=Path, default=None, help="answer key folder; enables scoring")
    r.add_argument("--as-of", default=None, help="run date for date-window rules (default: today)")
    r.add_argument("--max-quarantine-rate", type=float, default=0.05)
    r.add_argument("--force", action="store_true", help="build even if a table exceeds the quarantine limit")
    r.add_argument("--llm", default=None, help="optional Ollama model for column description leads")
    r.add_argument("--no-dbt", action="store_true")
    args = ap.parse_args(argv)
    pd.set_option("display.width", 140)
    sys.exit(cmd_run(args))
