"""The two runs most likely to embarrass the headline numbers.

1. Defect-rate sweep. Rules are learned from the same data they police, so a defect that is
   common enough stops looking like a defect. Regenerate the drop at rising defect rates and
   re-score discovery and validation.
2. Format shift. Re-encode three columns in formats the agent's parser vocabulary does not
   contain (European dates, decimal commas, timestamps with a UTC offset) and re-score types.

Writes outputs/stress.json and outputs/stress.md. Runs at scale 0.1 to stay in minutes.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd

from pipeline_builder import evaluate, validation
from pipeline_builder.datagen import generate
from pipeline_builder.discovery import discover, load_raw

AS_OF = date(2026, 2, 1)


def score_frozen(baseline: Path, root: Path) -> dict[str, Any]:
    """Rules learned once on the baseline drop, frozen, and enforced on a dirtier drop."""
    catalog, base_frames = discover(baseline / "raw")
    rules = validation.build_rules(catalog, base_frames, AS_OF)
    frames = {t: load_raw(root / "raw" / s.file) for t, s in catalog.tables.items()}
    _, defects = evaluate.load_truth(root / "ground_truth")
    dq = evaluate.score_flags(validation.run_rules(rules, catalog, frames), defects)
    return {
        "dq_recall": dq["recall"],
        "row_precision": dq["row_precision"],
        "by_defect": {k: v["recall"] for k, v in dq["by_defect"].items()},
    }


def score_drop(root: Path) -> dict[str, Any]:
    catalog, frames = discover(root / "raw")
    truth, defects = evaluate.load_truth(root / "ground_truth")
    agent_types = {t: {c.name: c.logical_type for c in s.columns} for t, s in catalog.tables.items()}
    types = evaluate.type_accuracy(agent_types, truth)
    pk = sum(catalog.tables[t].primary_key == s["primary_key"] for t, s in truth["tables"].items())
    true_fk = {(f["table"], f["column"]) for f in truth["foreign_keys"]}
    found_fk = {(f.table, f.column) for f in catalog.foreign_keys}
    rules = validation.build_rules(catalog, frames, AS_OF)
    dq = evaluate.score_flags(validation.run_rules(rules, catalog, frames), defects)
    return {
        "type_accuracy": types["accuracy"],
        "types_wrong": types["wrong"],
        "primary_keys": f"{pk}/{len(truth['tables'])}",
        "fk_recall": round(len(true_fk & found_fk) / len(true_fk), 3),
        "fk_spurious": len(found_fk - true_fk),
        "dq_recall": dq["recall"],
        "row_precision": dq["row_precision"],
        "by_defect": {k: v["recall"] for k, v in dq["by_defect"].items()},
    }


def shift_formats(raw: Path) -> list[str]:
    """Rewrite three columns into formats outside the parser vocabulary. Returns what changed."""
    cust = pd.read_csv(raw / "customers.csv", dtype=str, keep_default_na=False)
    d = pd.to_datetime(cust["signup_date"], format="mixed", errors="coerce")
    cust["signup_date"] = d.dt.strftime("%d.%m.%Y").fillna(cust["signup_date"])
    cust.to_csv(raw / "customers.csv", index=False)

    orders = pd.read_csv(raw / "orders.csv", dtype=str, keep_default_na=False)
    num = pd.to_numeric(orders["order_total"].str.replace(r"[$,]", "", regex=True), errors="coerce")
    euro = num.map(
        lambda x: f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".") if pd.notna(x) else ""
    )
    orders["order_total"] = euro.where(num.notna(), orders["order_total"])
    orders.to_csv(raw / "orders.csv", index=False)

    pay = pd.read_csv(raw / "payments.csv", dtype=str, keep_default_na=False)
    pay["paid_at"] = pay["paid_at"].where(pay["paid_at"] == "", pay["paid_at"] + "+04:00")
    pay.to_csv(raw / "payments.csv", index=False)
    return [
        "customers.signup_date -> 15.03.2024",
        "orders.order_total -> 1.234,50",
        "payments.paid_at -> +04:00",
    ]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--work", type=Path, default=Path("data/stress"))
    ap.add_argument("--scale", type=float, default=0.1)
    ap.add_argument("--report", type=Path, default=Path("outputs"))
    args = ap.parse_args()

    sweep, frozen = {}, {}
    baseline = args.work / "baseline"
    # Last month's drop: same business, different seed, ordinary defect rate.
    generate(baseline, scale=args.scale, defect_rate=0.005, seed=3)
    for rate in (0.005, 0.02, 0.05, 0.10):
        root = args.work / f"rate_{rate}"
        generate(root, scale=args.scale, defect_rate=rate, seed=7)
        sweep[rate] = score_drop(root)
        frozen[rate] = score_frozen(baseline, root)
        print(f"rate {rate:.3f}: {json.dumps({k: v for k, v in sweep[rate].items() if k != 'types_wrong'})}")
        print(f"   frozen contract: {json.dumps(frozen[rate])}")

    shifted = args.work / "format_shift"
    generate(shifted, scale=args.scale, defect_rate=0.005, seed=7)
    changes = shift_formats(shifted / "raw")
    shift = score_drop(shifted)
    print(f"format shift: types {shift['type_accuracy']}, wrong {shift['types_wrong']}")

    args.report.mkdir(parents=True, exist_ok=True)
    result = {
        "scale": args.scale,
        "sweep": {str(k): v for k, v in sweep.items()},
        "frozen_contract": {str(k): v for k, v in frozen.items()},
        "format_shift": shift,
    }
    result["format_shift"]["changes"] = changes
    (args.report / "stress.json").write_text(json.dumps(result, indent=2))

    kinds = sorted(next(iter(sweep.values()))["by_defect"])
    lines = [
        "# Stress runs",
        "",
        f"Scale {args.scale} (about {int(40_000 * args.scale / 0.1):,} orders). Run date {AS_OF}.",
        "",
        "## Defect-rate sweep",
        "",
        "| defect rate | types | PKs | FK recall | spurious FKs | DQ recall | row precision |",
        "|---|---|---|---|---|---|---|",
    ]
    for rate, r in sweep.items():
        lines.append(
            f"| {rate:.1%} | {r['type_accuracy']:.1%} | {r['primary_keys']} | {r['fk_recall']:.0%} "
            f"| {r['fk_spurious']} | {r['dq_recall']:.1%} | {r['row_precision']:.1%} |"
        )
    lines += [
        "",
        "| defect | " + " | ".join(f"{r:.1%}" for r in sweep) + " |",
        "|---" * (len(sweep) + 1) + "|",
    ]
    for k in kinds:
        lines.append(f"| {k} | " + " | ".join(f"{sweep[r]['by_defect'].get(k, 0):.0%}" for r in sweep) + " |")
    lines += [
        "",
        "## Same drops, rules frozen from last month's drop",
        "",
        "Rules learned once on a separate 0.5% drop (seed 3), then enforced unchanged.",
        "",
        "| defect rate | DQ recall | row precision | " + " | ".join(kinds) + " |",
        "|---" * (len(kinds) + 3) + "|",
    ]
    for rate, r in frozen.items():
        lines.append(
            f"| {rate:.1%} | {r['dq_recall']:.1%} | {r['row_precision']:.1%} | "
            + " | ".join(f"{r['by_defect'].get(k, 0):.0%}" for k in kinds)
            + " |"
        )
    lines += [
        "",
        "## Format shift",
        "",
        "Changed: " + "; ".join(changes) + ".",
        "",
        f"Type accuracy {shift['type_accuracy']:.1%}. Wrong: {', '.join(shift['types_wrong']) or 'none'}.",
        f"DQ recall {shift['dq_recall']:.1%}, row precision {shift['row_precision']:.1%}.",
    ]
    (args.report / "stress.md").write_text("\n".join(lines) + "\n")
    print((args.report / "stress.md").read_text())


if __name__ == "__main__":
    main()
