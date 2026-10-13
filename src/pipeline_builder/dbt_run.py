"""Run `dbt build` on the generated project and read back what passed."""

from __future__ import annotations

import json
import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class DbtResult:
    ok: bool
    models: Counter[str] = field(default_factory=Counter)
    tests: Counter[str] = field(default_factory=Counter)
    warned_tests: dict[str, int] = field(default_factory=dict)
    failed: list[str] = field(default_factory=list)
    seconds: float = 0.0


def run_dbt(project: Path, warehouse: Path) -> DbtResult:
    from dbt.cli.main import dbtRunner  # imported late: dbt is slow to import and not needed elsewhere

    os.environ["WAREHOUSE_PATH"] = str(warehouse.resolve())
    args = [
        "build",
        "--project-dir",
        str(project),
        "--profiles-dir",
        str(project),
        "--log-path",
        str(project / "logs"),
        "--target-path",
        str(project / "target"),
        "--quiet",
    ]
    res = dbtRunner().invoke(args)
    return read_results(project / "target" / "run_results.json", bool(res.success))


def read_results(path: Path, success: bool) -> DbtResult:
    if not path.exists():
        return DbtResult(ok=False, failed=["dbt produced no run_results.json"])
    data = json.loads(path.read_text())
    out = DbtResult(ok=success, seconds=round(float(data.get("elapsed_time", 0.0)), 1))
    for r in data["results"]:
        uid: str = r["unique_id"]
        status: str = r["status"]
        if uid.startswith("model."):
            out.models[status] += 1
        elif uid.startswith("test."):
            out.tests[status] += 1
            if status == "warn":
                out.warned_tests[uid.split(".")[2]] = int(r.get("failures") or 0)
        if status in ("error", "fail"):
            out.failed.append(f"{uid}: {r.get('message')}")
    return out
