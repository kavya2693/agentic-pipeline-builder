"""End to end on a small drop: the generated dbt project must build and agree with pandas."""

import json
import sys
import types
from pathlib import Path
from typing import Any

import duckdb
import pytest

from pipeline_builder.cli import sql_vs_pandas
from pipeline_builder.orchestrator import RunConfig, RunContext, final_state, run

from .conftest import AS_OF


def stub_model(prompt: str) -> str:
    """Stands in for the local model: a fixed lead per column, read from the prompt."""
    cols = [line[2:].split(" ")[0] for line in prompt.splitlines() if line.startswith("- ")]
    return json.dumps({c: f"what {c} holds" for c in cols})


@pytest.fixture(scope="module")
def built(drop: Path, tmp_path_factory: pytest.TempPathFactory) -> RunContext:
    out = tmp_path_factory.mktemp("gen")
    return run(RunConfig(raw=drop / "raw", out=out, as_of=AS_OF, generate=stub_model))


def test_run_reaches_done_and_dbt_passes(built: RunContext) -> None:
    assert final_state(built) == "DONE", built.log
    assert built.dbt is not None and built.dbt.ok
    assert set(built.dbt.models) == {"success"}
    # Relationship tests may warn (a child of a quarantined parent); nothing may fail.
    assert set(built.dbt.tests) <= {"pass", "warn"}


def test_dbt_quarantine_matches_pandas_rule_runner_row_for_row(built: RunContext) -> None:
    for table, counts in sql_vs_pandas(built).items():
        assert counts["only_sql"] == 0 and counts["only_pandas"] == 0, (table, counts)


def test_marts_hold_only_clean_rows(built: RunContext) -> None:
    with duckdb.connect(str((built.cfg.out / "warehouse.duckdb").resolve())) as con:
        dupes = con.execute(
            "select count(*) from (select order_id from main.fct_orders group by 1 having count(*) > 1)"
        ).fetchone()
        stray = con.execute("select count(*) from main.fct_orders where status = 'shiped'").fetchone()
    assert dupes == (0,) and stray == (0,)


def test_model_leads_are_used_and_facts_kept(built: RunContext) -> None:
    schema = (built.cfg.out / "dbt" / "models" / "staging" / "schema.yml").read_text()
    assert "what customer_id holds. Integer. Primary key." in schema
    assert built.descriptions is not None and built.descriptions.model_leads > 0


def _airflow_stub() -> dict[str, Any]:
    """Minimal stand-ins for the airflow modules the DAG imports; records tasks and edges."""
    edges: list[tuple[str, str]] = []
    tasks: dict[str, Any] = {}

    class Op:
        def __init__(self, task_id: str, **kw: Any) -> None:
            self.task_id, self.kw = task_id, kw
            tasks[task_id] = self

    def chain(*ops: Any) -> None:
        for a, b in zip(ops, ops[1:], strict=False):
            edges.append((a.task_id, b.task_id))

    class DAG:
        def __init__(self, **kw: Any) -> None:
            self.kw = kw

        def __enter__(self) -> "DAG":
            return self

        def __exit__(self, *a: object) -> None:
            return None

    mods = {
        "airflow": types.SimpleNamespace(DAG=DAG),
        "airflow.models": types.SimpleNamespace(),
        "airflow.models.baseoperator": types.SimpleNamespace(chain=chain),
        "airflow.operators": types.SimpleNamespace(),
        "airflow.operators.bash": types.SimpleNamespace(BashOperator=Op),
        "airflow.operators.python": types.SimpleNamespace(PythonOperator=Op),
    }
    return {"modules": mods, "edges": edges, "tasks": tasks}


def test_generated_dag_wires_land_then_validate_then_dbt(built: RunContext, monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _airflow_stub()
    for name, mod in stub["modules"].items():
        monkeypatch.setitem(sys.modules, name, mod)
    code = (built.cfg.out / "airflow" / "dags" / "raw_drop_pipeline.py").read_text()
    exec(compile(code, "raw_drop_pipeline.py", "exec"), {})
    order = [stub["edges"][0][0]] + [b for _, b in stub["edges"]]
    assert order[-2:] == ["validate_drop", "dbt_build"]
    assert sorted(order[:-2]) == sorted(f"land_{t}" for t in built.catalog.tables)  # type: ignore[union-attr]
    assert "dbt build" in stub["tasks"]["dbt_build"].kw["bash_command"]


def test_gate_halts_before_building_when_quarantine_is_too_high(drop: Path, tmp_path: Path) -> None:
    ctx = run(RunConfig(raw=drop / "raw", out=tmp_path, as_of=AS_OF, max_quarantine_rate=0.001))
    assert final_state(ctx) == "HALTED"
    assert not (tmp_path / "dbt").exists()
    assert (tmp_path / "rules.yml").exists()


def test_broken_model_output_falls_back_to_rule_text(drop: Path, tmp_path: Path) -> None:
    ctx = run(RunConfig(raw=drop / "raw", out=tmp_path, as_of=AS_OF, generate=lambda p: "not json", run_dbt=False))
    assert final_state(ctx) == "DONE"
    assert ctx.descriptions is not None
    assert ctx.descriptions.model_leads == 0 and ctx.descriptions.model_rejected > 0
