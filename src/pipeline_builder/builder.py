"""Builder agent: turn the catalog and the rule file into a dbt project and an Airflow DAG.

The generated project has three layers:

- `stg_<table>__checked` parses every raw text column and evaluates every validation rule in
  SQL, recording the ids of the rules each row fails.
- `stg_<table>` keeps rows that fail no blocking rule; `stg_<table>__rejected` keeps the rest
  with their reasons, so nothing is dropped without a record.
- marts: a `dim_` model per table that is referenced but references nothing, a `fct_` model per
  table that references others (with one hop of parent attributes), and a daily aggregate
  per fact that has its own date.

Mart shape is decided by the relationship graph alone. That is a deliberate limit: the
builder does not know which measures a business cares about.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from pipeline_builder import sql
from pipeline_builder.describe import Descriptions
from pipeline_builder.discovery import Catalog, TableSchema, _singular
from pipeline_builder.typing_rules import NULL_TOKENS
from pipeline_builder.validation import Rule

PROJECT = "raw_drop_pipeline"


@dataclass
class BuildPlan:
    dims: list[str] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)
    daily: dict[str, tuple[str, list[str]]] = field(default_factory=dict)  # fact -> (date col, measures)


def plan_marts(catalog: Catalog) -> BuildPlan:
    referencing = {f.table for f in catalog.foreign_keys}
    referenced = {f.ref_table for f in catalog.foreign_keys}
    plan = BuildPlan(
        dims=sorted(referenced - referencing),
        facts=sorted(referencing),
    )
    for t in plan.facts:
        schema = catalog.tables[t]
        dates = [c for c in schema.columns if c.logical_type in ("date", "timestamp")]
        measures = [
            c.name
            for c in schema.columns
            if c.logical_type == "decimal" and not c.is_identifier and c.name not in schema.primary_key
        ]
        if dates and measures:
            plan.daily[t] = (dates[0].name, measures)
    return plan


def categorical_columns(rules: list[Rule], table: str) -> set[str]:
    return {r.column for r in rules if r.table == table and r.expectation == "expect_column_values_to_be_in_set"}


def checked_model(schema: TableSchema, rules: list[Rule], catalog: Catalog) -> str:
    t_rules = [r for r in rules if r.table == schema.name]
    lines = ["{{ config(materialized='table') }}", "", "with source as (", f"    select * from {{{{ source('raw', '{schema.name}') }}}}", "),", ""]
    lines.append("cleaned as (\n    select\n        _row_id,")
    lines.append(",\n".join(f"        {{{{ clean_text('{c.name}') }}}} as {sql.text_col(c.name)}" for c in schema.columns))
    lines.append("    from source\n),\n")
    pk = schema.primary_key
    lines.append("typed as (\n    select\n        *,")
    typed = [f"        {sql.parse_expr(c)} as {c.name}" for c in schema.columns]
    if pk:
        part = ", ".join(sql.text_col(k) for k in pk)
        nonnull = " and ".join(f"{sql.text_col(k)} is not null" for k in pk)
        typed.append(
            f"        case when {nonnull} then row_number() over (partition by {part} order by _row_id) "
            "else 1 end as _key_seq"
        )
    lines.append(",\n".join(typed))
    lines.append("    from cleaned\n),\n")

    refs = [r for r in t_rules if r.expectation == "expect_column_values_to_exist_in"]
    aliases: dict[str, str] = {}
    for i, r in enumerate(refs):
        alias = f"parent_{i}"
        aliases[r.id] = alias
        parent = catalog.tables[r.kwargs["ref_table"]]
        pcol = parent.column(r.kwargs["ref_column"])
        expr = sql.parse_expr(pcol).replace(sql.text_col(pcol.name), f"{{{{ clean_text('{pcol.name}') }}}}")
        lines.append(
            f"{alias} as (\n    select distinct {expr} as k\n"
            f"    from {{{{ source('raw', '{parent.name}') }}}}\n),\n"
        )

    def rule_list(severity: str) -> str:
        items = [
            f"            case when {sql.violation_expr(r, aliases.get(r.id))} then {sql.lit(r.id)} end"
            for r in t_rules
            if r.severity == severity
        ]
        if not items:
            return "[]::varchar[]"
        return "list_filter([\n" + ",\n".join(items) + "\n        ], x -> x is not null)"

    lines.append("flagged as (\n    select\n        typed.*,")
    lines.append(f"        {rule_list('block')} as failed_rules,")
    lines.append(f"        {rule_list('warn')} as warned_rules")
    lines.append("    from typed")
    for r in refs:
        a = aliases[r.id]
        lines.append(f"    left join {a} on {a}.k = typed.{r.column}")
    lines.append(")\n\nselect * from flagged")
    return "\n".join(lines) + "\n"


def staging_model(schema: TableSchema, categorical: set[str]) -> str:
    cols = ",\n".join(
        f"    lower({c.name}) as {c.name}" if c.name in categorical else f"    {c.name}" for c in schema.columns
    )
    return (
        f"-- Rows of {schema.file} that pass every blocking rule, typed and standardised.\n"
        f"select\n    _row_id,\n{cols},\n    len(warned_rules) > 0 as has_warnings\n"
        f"from {{{{ ref('stg_{schema.name}__checked') }}}}\nwhere len(failed_rules) = 0\n"
    )


def rejected_model(schema: TableSchema) -> str:
    texts = ",\n".join(f"    {sql.text_col(c.name)} as {c.name}" for c in schema.columns)
    return (
        f"-- Rows of {schema.file} quarantined by at least one blocking rule, with the raw text kept.\n"
        f"select\n    _row_id,\n    failed_rules,\n{texts}\n"
        f"from {{{{ ref('stg_{schema.name}__checked') }}}}\nwhere len(failed_rules) > 0\n"
    )


def fact_model(table: str, catalog: Catalog, plan: BuildPlan) -> str:
    schema = catalog.tables[table]
    own = {c.name for c in schema.columns}
    select = ["    f.*"]
    joins = []
    for i, fk in enumerate(catalog.parents_of(table)):
        if fk.ref_table not in plan.facts:
            continue
        parent = catalog.tables[fk.ref_table]
        # One hop of the parent event's context: its date and the keys it points at.
        carry = [
            c.name
            for c in parent.columns
            if c.name not in own and c.name not in parent.primary_key
            and (c.logical_type in ("date", "timestamp") or any(
                p.column == c.name for p in catalog.parents_of(parent.name)
            ))
        ]
        if not carry:
            continue
        alias = f"p{i}"
        own.update(carry)
        select.extend(f"    {alias}.{c}" for c in carry)
        joins.append(
            f"left join {{{{ ref('stg_{parent.name}') }}}} as {alias} on {alias}.{fk.ref_column} = f.{fk.column}"
        )
    body = ",\n".join(select)
    join_sql = ("\n" + "\n".join(joins)) if joins else ""
    return (
        f"{{{{ config(materialized='table') }}}}\n\n"
        f"select\n{body}\nfrom {{{{ ref('stg_{table}') }}}} as f{join_sql}\n"
    )


def dim_model(table: str) -> str:
    return f"{{{{ config(materialized='table') }}}}\n\nselect * from {{{{ ref('stg_{table}') }}}}\n"


def daily_model(table: str, date_col: str, measures: list[str]) -> str:
    sums = ",\n".join(f"    sum({m}) as {m}" for m in measures)
    return (
        f"{{{{ config(materialized='table') }}}}\n\n"
        f"select\n    cast({date_col} as date) as day,\n    count(*) as rows,\n{sums}\n"
        f"from {{{{ ref('stg_{table}') }}}}\ngroup by 1\norder by 1\n"
    )


def _test(name: str, severity: str | None = None, **arguments: object) -> dict[str, object] | str:
    if not arguments and not severity:
        return name
    body: dict[str, object] = {}
    if arguments:
        body["arguments"] = arguments
    if severity:
        body["config"] = {"severity": severity}
    return {name: body}


def staging_schema(catalog: Catalog, rules: list[Rule], desc: Descriptions) -> dict[str, object]:
    models = []
    for t, schema in catalog.tables.items():
        cat = categorical_columns(rules, t)
        required = {
            r.column for r in rules if r.table == t and r.expectation == "expect_column_values_to_not_be_null"
        }
        model_tests: list[object] = []
        if len(schema.primary_key) > 1:
            model_tests.append(_test("unique_combination", combination=schema.primary_key))
        columns = []
        for c in schema.columns:
            tests: list[object] = []
            if schema.primary_key == [c.name]:
                tests.append("unique")
            if c.name in required:
                tests.append("not_null")
            for r in rules:
                if r.table != t or r.column != c.name:
                    continue
                if r.expectation == "expect_column_values_to_be_in_set":
                    tests.append(_test("accepted_values", values=r.kwargs["value_set"]))
                if r.expectation == "expect_column_values_to_exist_in":
                    # Warn, not error: a child row can point at a parent that was itself
                    # quarantined. The count is the cascade, reported rather than hidden.
                    tests.append(
                        _test(
                            "relationships",
                            severity="warn",
                            to=f"ref('stg_{r.kwargs['ref_table']}')",
                            field=r.kwargs["ref_column"],
                        )
                    )
            entry: dict[str, object] = {"name": c.name, "description": desc.column(t, c.name)}
            if c.name in cat:
                entry["description"] = desc.column(t, c.name) + " Standardised to lower case."
            if tests:
                entry["data_tests"] = tests
            columns.append(entry)
        model: dict[str, object] = {"name": f"stg_{t}", "description": desc.table(t), "columns": columns}
        if model_tests:
            model["data_tests"] = model_tests
        models.append(model)
        models.append(
            {
                "name": f"stg_{t}__rejected",
                "description": f"Quarantined rows of {schema.file} and the rule ids they failed.",
            }
        )
        models.append(
            {
                "name": f"stg_{t}__checked",
                "description": f"Every row of {schema.file}, parsed, with failed and warned rule ids.",
            }
        )
    return {"version": 2, "models": models}


def marts_schema(catalog: Catalog, plan: BuildPlan, desc: Descriptions) -> dict[str, object]:
    models: list[dict[str, object]] = []
    for t in plan.dims:
        schema = catalog.tables[t]
        cols = [{"name": k, "data_tests": ["unique", "not_null"]} for k in schema.primary_key[:1]]
        models.append({"name": f"dim_{_singular(t)}", "description": desc.table(t), "columns": cols})
    for t in plan.facts:
        schema = catalog.tables[t]
        cols: list[dict[str, object]] = []
        tests: list[object] = []
        if len(schema.primary_key) == 1:
            cols.append({"name": schema.primary_key[0], "data_tests": ["unique", "not_null"]})
        elif schema.primary_key:
            tests.append(_test("unique_combination", combination=schema.primary_key))
        for fk in catalog.parents_of(t):
            if fk.ref_table in plan.dims:
                cols.append(
                    {
                        "name": fk.column,
                        "data_tests": [
                            _test(
                                "relationships",
                                severity="warn",
                                to=f"ref('dim_{_singular(fk.ref_table)}')",
                                field=fk.ref_column,
                            )
                        ],
                    }
                )
        m: dict[str, object] = {"name": f"fct_{t}", "description": desc.table(t), "columns": cols}
        if tests:
            m["data_tests"] = tests
        models.append(m)
    for t, (d, _) in plan.daily.items():
        models.append(
            {
                "name": f"agg_{t}_daily",
                "description": f"Daily totals of {t} by {d}.",
                "columns": [{"name": "day", "data_tests": ["unique", "not_null"]}],
            }
        )
    return {"version": 2, "models": models}


def sources_yaml(catalog: Catalog, desc: Descriptions) -> dict[str, object]:
    return {
        "version": 2,
        "sources": [
            {
                "name": "raw",
                "schema": "raw",
                "description": "Raw files landed as text, one table per file, with the file row position in _row_id.",
                "tables": [
                    {"name": t, "description": f"Landed from {s.file}. {desc.table(t)}"}
                    for t, s in catalog.tables.items()
                ],
            }
        ],
    }


CLEAN_TEXT_MACRO = """{%% macro clean_text(column) -%%}
    case
        when {{ column }} is null or lower(trim({{ column }})) in (%s) then null
        else trim({{ column }})
    end
{%%- endmacro %%}
"""

UNIQUE_COMBINATION_TEST = """{% test unique_combination(model, combination) %}
select {{ combination | join(', ') }}
from {{ model }}
group by {{ combination | join(', ') }}
having count(*) > 1
{% endtest %}
"""


def _dump(path: Path, content: str | dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, dict):
        content = yaml.safe_dump(content, sort_keys=False, width=110)
    path.write_text(content)


def write_dbt_project(
    out: Path, catalog: Catalog, rules: list[Rule], desc: Descriptions, warehouse: Path
) -> BuildPlan:
    plan = plan_marts(catalog)
    _dump(
        out / "dbt_project.yml",
        {
            "name": PROJECT,
            "version": "1.0.0",
            "profile": PROJECT,
            "model-paths": ["models"],
            "macro-paths": ["macros"],
            "models": {PROJECT: {"staging": {"+materialized": "view"}, "marts": {"+materialized": "table"}}},
        },
    )
    _dump(
        out / "profiles.yml",
        {
            PROJECT: {
                "target": "dev",
                "outputs": {
                    "dev": {
                        "type": "duckdb",
                        "path": "{{ env_var('WAREHOUSE_PATH', '" + str(warehouse) + "') }}",
                        "threads": 4,
                    }
                },
            }
        },
    )
    tokens = ", ".join(sql.lit(x) for x in sorted(NULL_TOKENS))
    _dump(out / "macros" / "clean_text.sql", CLEAN_TEXT_MACRO % tokens)
    _dump(out / "macros" / "test_unique_combination.sql", UNIQUE_COMBINATION_TEST)
    _dump(out / "models" / "sources.yml", sources_yaml(catalog, desc))
    for t, schema in catalog.tables.items():
        stg = out / "models" / "staging"
        _dump(stg / f"stg_{t}__checked.sql", checked_model(schema, rules, catalog))
        _dump(stg / f"stg_{t}.sql", staging_model(schema, categorical_columns(rules, t)))
        _dump(stg / f"stg_{t}__rejected.sql", rejected_model(schema))
    _dump(out / "models" / "staging" / "schema.yml", staging_schema(catalog, rules, desc))
    marts = out / "models" / "marts"
    for t in plan.dims:
        _dump(marts / f"dim_{_singular(t)}.sql", dim_model(t))
    for t in plan.facts:
        _dump(marts / f"fct_{t}.sql", fact_model(t, catalog, plan))
    for t, (d, measures) in plan.daily.items():
        _dump(marts / f"agg_{t}_daily.sql", daily_model(t, d, measures))
    _dump(marts / "schema.yml", marts_schema(catalog, plan, desc))
    return plan


DAG_TEMPLATE = '''"""Nightly raw-drop pipeline. Generated by pipeline-builder from {n_tables} source files.

land_<table>  copy one raw file into the warehouse as text, keeping file row positions
validate_drop run the validation rules and stop if any table quarantines too much
dbt_build     build and test staging, quarantine and mart models
"""

from datetime import datetime, timedelta

from airflow import DAG
from airflow.models.baseoperator import chain
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

from pipeline_builder.runtime import land_source, validate_drop

RAW_DIR = "{raw_dir}"
WAREHOUSE = "{warehouse}"
DBT_DIR = "{dbt_dir}"
RULES = "{rules}"
CATALOG = "{catalog}"

with DAG(
    dag_id="{dag_id}",
    schedule="0 3 * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    max_active_runs=1,
    default_args={{"retries": 2, "retry_delay": timedelta(minutes=5)}},
    tags=["generated", "raw-drop"],
) as dag:
{land_tasks}

    validate = PythonOperator(
        task_id="validate_drop",
        python_callable=validate_drop,
        op_kwargs={{
            "raw_dir": RAW_DIR,
            "catalog_path": CATALOG,
            "rules_path": RULES,
            "max_quarantine_rate": {max_rate},
        }},
    )

    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=f"dbt build --project-dir {{DBT_DIR}} --profiles-dir {{DBT_DIR}}",
        env={{"WAREHOUSE_PATH": WAREHOUSE}},
        append_env=True,
    )

    # Landing runs one file at a time: DuckDB allows a single writing process per database
    # file, so parallel land tasks would fail on the lock.
    chain({land_ids}, validate, dbt_build)
'''


def write_airflow_dag(
    out: Path,
    catalog: Catalog,
    raw_dir: Path,
    warehouse: Path,
    dbt_dir: Path,
    rules_path: Path,
    catalog_path: Path,
    max_rate: float,
) -> Path:
    tasks = []
    ids = []
    for t, schema in catalog.tables.items():
        var = f"land_{t}"
        ids.append(var)
        tasks.append(
            f"    {var} = PythonOperator(\n"
            f'        task_id="{var}",\n'
            "        python_callable=land_source,\n"
            f'        op_kwargs={{"path": f"{{RAW_DIR}}/{schema.file}", "table": "{t}", "warehouse": WAREHOUSE}},\n'
            "    )"
        )
    code = DAG_TEMPLATE.format(
        n_tables=len(catalog.tables),
        raw_dir=raw_dir,
        warehouse=warehouse,
        dbt_dir=dbt_dir,
        rules=rules_path,
        catalog=catalog_path,
        dag_id=PROJECT,
        land_tasks="\n".join(tasks),
        land_ids=", ".join(ids),
        max_rate=max_rate,
    )
    path = out / f"{PROJECT}.py"
    _dump(path, code)
    return path
