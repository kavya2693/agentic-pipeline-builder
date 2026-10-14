# agentic-pipeline-builder

[![ci](https://github.com/kavya2693/agentic-pipeline-builder/actions/workflows/ci.yml/badge.svg)](https://github.com/kavya2693/agentic-pipeline-builder/actions/workflows/ci.yml)
![python](https://img.shields.io/badge/python-3.11-blue)
![license](https://img.shields.io/badge/license-MIT-green)

A folder of messy source exports in; a typed, tested dbt project, a quarantine for bad
rows and an Airflow DAG out, with every inferred type, key and rule checked against a
known answer.

[![86-second silent walkthrough: the onboarding problem, how the agents work, the architecture, the run log, the orders contract and the results](docs/demo/demo.gif)](docs/demo/demo.mp4)

*~86-second silent walkthrough: the problem, the three agents, the run and what didn't win. Click for the full video ([MP4, 5 MB](docs/demo/demo.mp4)).*

📄 **Audit report:** every figure and assumption in the video, traced to its source — [PDF](docs/demo/AUDIT_REPORT.pdf) · [Word](docs/demo/AUDIT_REPORT.docx) · [Markdown](docs/demo/AUDIT_REPORT.md)

## At a glance

| | |
|---|---|
| **Problem** | Onboarding a new source system means days per source of manual detective work (real column types, keys, joins, bad values) before a single staging model is written, and the guesses made there break pipelines quietly. |
| **Approach** | Three deterministic agents in a state machine: discovery infers types, keys and joins with the evidence for each; validation writes data-quality rules as YAML; the builder compiles them into a dbt project with a quarantine and an Airflow DAG. The first run's rules become a reviewed contract that later drops are checked against. |
| **Result** | On a 1.86M-row, 7-file drop: **54/54 column types** (DuckDB auto-detect 44/54, pandas 37/54), 7/7 primary and foreign keys; **88.6% of 45,677 injected defects caught at 98.4% row precision**; dbt SQL and the pandas runner quarantine the same 39,666 rows; raw folder to green `dbt build` in 100 s. |
| **What didn't win** | Rules re-learned from each drop collapse from 93.0% to **20.8% recall** once defects pass 2% per type (the frozen contract holds 91.3%). Prices inflated 100x are caught 22.8% of the time, category typos 63.4%. Unknown date and number formats fall back to text without a warning. The optional local model's column leads were rejected for 26 of 54 columns. |

## Architecture

![Animated architecture: raw drop through discovery, validation, the quarantine gate, the builder and dbt build](docs/demo/architecture.gif)

```mermaid
flowchart LR
    subgraph SRC["Sources"]
        RAW[("Nightly raw drop<br/>csv · json · jsonl · parquet")]
        CON[("Reviewed contract<br/>catalog.yml + rules.yml")]
        GT[("Answer key<br/>data/ground_truth/<br/>datagen.py")]
    end

    subgraph DATA["Data and typing"]
        LOAD["Loader, every value as text<br/>discovery.py · load_raw"]
        PARSE["Type parsers and formats<br/>typing_rules.py"]
    end

    subgraph AGENTS["Agents, deterministic"]
        DISC["Discovery agent<br/>types · keys · relationships<br/>discovery.py"]
        VAL["Validation agent<br/>profile to expectations<br/>validation.py"]
        DESC["Column descriptions<br/>optional local model<br/>describe.py"]
    end

    subgraph DEC["Decision and build"]
        ORCH["State machine<br/>TRANSITIONS · MAX_ATTEMPTS<br/>orchestrator.py"]
        GATE{"Quarantine gate<br/>any table over 5%?"}
        BUILD["Builder<br/>dbt models · schema.yml · DAG<br/>builder.py + sql.py"]
        LAND["Landing<br/>raw.* as text + _row_id<br/>runtime.py"]
        HALT["HALTED<br/>nothing built"]
    end

    subgraph OUT["Outputs"]
        CAT[("catalog.yml")]
        RULES[("rules.yml<br/>136 rules + evidence")]
        DBT[("dbt project<br/>stg_*__checked · stg_* · stg_*__rejected<br/>dim_* · fct_* · agg_*_daily")]
        DAG[("Airflow DAG<br/>raw_drop_pipeline.py")]
        WH[("warehouse.duckdb")]
        LOG[("run_state.json")]
    end

    subgraph EVAL["Evaluation and monitoring"]
        DBTRUN["dbt build on DuckDB<br/>dbt_run.py"]
        PARITY["SQL vs pandas parity<br/>cli.py · sql_vs_pandas"]
        SCORE["Scorer vs answer key<br/>evaluate.py"]
        REP[("outputs/<br/>metrics.json · report.md")]
    end

    RAW -->|"files"| LOAD
    LOAD -->|"text frames"| DISC
    PARSE -->|"parse rates, formats"| DISC
    DISC -->|"types, keys, FKs"| CAT
    CAT -->|"schema"| VAL
    LOAD -->|"text frames"| VAL
    VAL -->|"expectations"| RULES
    VAL -->|"quarantine rate per table"| GATE
    GATE -->|"yes"| HALT
    GATE -->|"no"| BUILD
    RULES -->|"rules compiled to SQL"| BUILD
    DESC -.->|"wording only, falls back to rule text"| BUILD
    BUILD -->|"models + tests"| DBT
    BUILD -->|"land, validate, build tasks"| DAG
    BUILD --> LAND
    LAND -->|"raw.* tables"| WH
    WH --> DBTRUN
    DBT --> DBTRUN
    DBTRUN -->|"run_results.json, retried once"| PARITY
    PARITY -->|"row-for-row agreement"| REP
    GT -->|"true types, keys, defect rows"| SCORE
    RULES -->|"pandas flags"| SCORE
    SCORE -->|"recall, precision"| REP
    ORCH -.->|"every state, attempt, duration"| LOG
    RULES -.->|"person reviews and edits"| CON
    CON -.->|"contract flag: frozen catalog"| DISC
    CON -.->|"contract flag: frozen rules"| VAL

    classDef data fill:#7fa8ff,stroke:#3d64c4,color:#0b1020
    classDef models fill:#b9a2ff,stroke:#6f55c9,color:#0b1020
    classDef decision fill:#3fd8a8,stroke:#1f8f6c,color:#0b1020
    classDef evaluation fill:#f2c46d,stroke:#b08528,color:#0b1020
    classDef risk fill:#ff8a73,stroke:#c4503a,color:#0b1020

    class RAW,CON,GT,LOAD,PARSE,CAT,RULES,DBT,DAG,WH,LOG data
    class DISC,VAL,DESC models
    class ORCH,BUILD,LAND decision
    class DBTRUN,PARITY,SCORE,REP evaluation
    class GATE,HALT risk
```

Solid edges are the nightly path; dotted edges are the optional model, the run log, and the review loop that turns one run's rules into the next run's contract.

### Components

| Component | Responsibility | File |
|---|---|---|
| Orchestrator | Runs the states DISCOVER → VALIDATE → BUILD → LAND → DBT_BUILD from a `(state, event) → next state` table, owns retries and the quarantine stop, writes `run_state.json` | [orchestrator.py](src/pipeline_builder/orchestrator.py) |
| Loader and parsers | Reads every file as text; one definition of each logical type, its accepted text forms and date formats, shared by discovery, the pandas checks and the SQL | [discovery.py](src/pipeline_builder/discovery.py), [typing_rules.py](src/pipeline_builder/typing_rules.py) |
| Discovery agent | Infers types from parse rates (98% threshold), keys from null and distinct rates, foreign keys from name tokens plus 90% row containment | [discovery.py](src/pipeline_builder/discovery.py) |
| Validation agent | Profiles each column and writes Great Expectations-style rules with evidence and a `block` or `warn` severity; runs them in pandas and summarises quarantine per table | [validation.py](src/pipeline_builder/validation.py) |
| Builder | Compiles the same rules to DuckDB SQL (`__checked`, `stg_`, `__rejected`), derives `dim_`/`fct_`/`agg_` marts from the relationship graph, writes `schema.yml` tests and the Airflow DAG | [builder.py](src/pipeline_builder/builder.py), [sql.py](src/pipeline_builder/sql.py) |
| Landing and DAG tasks | Lands each frame into `raw.<table>` as text with its file row position; the generated DAG imports the same land and validate functions | [runtime.py](src/pipeline_builder/runtime.py) |
| dbt runner | Invokes `dbt build` in-process and reads `run_results.json` into model and test counts | [dbt_run.py](src/pipeline_builder/dbt_run.py) |
| Description writer | Optional Ollama call for one-line column leads; key, reference and allowed-value facts are always appended by code | [describe.py](src/pipeline_builder/describe.py) |
| Scorer | Scores types, keys, relationships and defect recall/precision against the answer key, plus the pandas and DuckDB baselines | [evaluate.py](src/pipeline_builder/evaluate.py) |
| CLI and report | `pipeline-builder run`, SQL vs pandas parity check, `outputs/metrics.json` and `report.md` | [cli.py](src/pipeline_builder/cli.py) |
| Data generator | Seeded nightly drop with nine defect types injected at known rows, and the answer key | [datagen.py](src/pipeline_builder/datagen.py) |

### Design decisions

Full reasoning in [ADR-0001](docs/adr/0001-deterministic-agents-reviewed-contract.md).

| Decision | Options considered | Chosen | Trade-off |
|---|---|---|---|
| Who infers schema and writes SQL | A model; deterministic code with named thresholds | Deterministic code; a model only words descriptions | Reproducible and reviewable line by line, but the parser vocabulary is a fixed list that has to grow by hand |
| Where later runs get their rules | Re-learn from each drop; freeze a reviewed contract | Reviewed contract via `--contract` | Recall holds at 91% on dirty drops instead of 21%, but the first contract needs a person to read `rules.yml` |
| Rule format and runtime | Great Expectations as a dependency; own GX-style YAML compiled to dbt SQL | Own YAML, compiled to SQL | One rule source for pandas and SQL without the GX runtime; covers nine expectation types, not GX's catalogue |
| What happens to a bad row | Drop in staging; quarantine with reasons | `stg_<table>__rejected` with `failed_rules` | More models per table, but the source owner can be told exactly which rows to fix |
| Mart joins | Inner joins; left joins with warn-level relationship tests | Left joins, warn severity | A quarantined parent does not silently delete children; the cascade is counted (22,619 order lines) instead of hidden |
| Trusting the scored numbers | Score pandas only; require SQL and pandas to agree | Both, with a row-for-row test | Rules are implemented twice, but the scored recall is a statement about the SQL that would run in production |

### Data contracts

| Stage | Artifact | Shape |
|---|---|---|
| Input | raw folder | one file per table (`.csv`, `.json`, `.jsonl`, `.parquet`); table name is the file stem; every value read as text |
| Discovery out | `catalog.yml` | per table: `name`, `file`, `row_count`, `primary_key: [col]`; per column: `name`, `logical_type` (boolean, integer, decimal, date, timestamp, string), `formats: [strptime]`, `null_rate`, `distinct_ratio`, `is_identifier`, `description`; `foreign_keys`: `table`, `column`, `ref_table`, `ref_column`, `containment`, `named_match` |
| Validation out | `rules.yml` | per rule: `id`, `table`, `expectation`, `columns: [col]`, `kwargs`, `severity` (block or warn), `evidence` |
| Validation, in memory | flags and summary | flags: `table`, `row`, `column`, `rule_id`, `expectation`, `severity`; summary: `table`, `rows`, `quarantined`, `quarantine_rate`, `warned` |
| Landing | `raw.<table>` in DuckDB | `_row_id bigint` (row position in the file) plus every source column as `varchar` |
| Staging | `stg_<table>__checked`, `stg_<table>`, `stg_<table>__rejected` | typed columns plus `<col>__text` and `failed_rules` (list of rule ids); clean rows are typed, rejected rows keep the raw text |
| Run log | `run_state.json` | per step: `state`, `attempt`, `event`, `seconds`, `detail` |

### Failure modes and fallbacks

| Situation | What the code does |
|---|---|
| Folder empty, or a file named in the contract is missing (for example a late drop) | DISCOVER returns `empty` and the run ends `FAILED`. There is no wait-and-retry for late files. |
| Any table would quarantine more than 5% of its rows | Run stops at `HALTED` before anything is built (`--force` overrides). The generated DAG's `validate_drop` task raises `QuarantineThresholdError` on the same condition. |
| A state raises | LAND and DBT_BUILD are retried once (`MAX_ATTEMPTS = 2`); other states fail fast, since a rule that fails on the data fails the same way twice. Any unmapped event goes to `FAILED`. The scheduled DAG retries tasks twice, 5 minutes apart. |
| dbt writes no `run_results.json` | Recorded as a failed build with that message; run ends `FAILED`. |
| Description model down, slow or returning junk | 120 s request timeout; the exception is caught and every column falls back to rule-derived text. The build never waits on the model. |
| Parent rows quarantined | Children are kept by left joins; warn-level relationship tests count the orphans. |
| Value format outside the parser vocabulary | **Gap:** on a first run the column becomes text without a warning. Under a frozen contract the type rule trips on every row and the gate halts the run. |
| Source with more than about 1% of a defect on a first run | **Gap:** the rule that would catch it is never written, silently. Fixed on later runs by the reviewed contract; the first contract needs a person. |

### Path to production

This is a mapping, not a deployed system. The orchestrator's transition table already has the shape of an Amazon States Language definition; [docs/aws-mapping.md](docs/aws-mapping.md) has the state-by-state detail.

| Local | Production equivalent |
|---|---|
| `data/raw/` folder | S3 prefix with `ObjectCreated` events to SQS, batching one nightly drop into one execution |
| `orchestrator.py` transition table, `MAX_ATTEMPTS` | Step Functions state machine with `Retry` on `States.TaskFailed` |
| DISCOVER per file | `Map` state, one Lambda per file, then a reduce step for relationships |
| `HALTED` gate | `Choice` state into a `.waitForTaskToken` task; a person approves or edits the contract |
| DuckDB `warehouse.duckdb`, dbt-duckdb | Athena or Redshift with dbt-athena or dbt-redshift; `try_strptime` and `regexp_full_match` become `try(date_parse(...))` and `regexp_like` |
| `dbt build` in-process | ECS Fargate task |
| Generated Airflow DAG (`0 3 * * *`) | Managed Airflow (MWAA), or replaced by the Step Functions schedule |
| Ollama on a laptop | Managed model endpoint, still off the critical path |
| `run_state.json` | Step Functions execution history |
| `FAILED` | `Fail` state plus an EventBridge alert |
| `outputs/metrics.json` quarantine rates | CloudWatch metrics per table with alarms on a rising rate |
| Re-learning rules | Deliberately manual: a new contract is proposed by a fresh run and merged after review, never adopted automatically |

## The problem

A data team is told that a new source system is "ready": a shared folder now gets a
nightly drop of seven files from a retailer's order, payment, returns and catalogue
systems. CSV from two of them, JSON from the catalogue, Parquet from the order-line
service. Before anyone can report on it, an engineer has to open every file and work out
what each column really is (that `postal_code` is text even though it looks numeric, that
`signup_date` is in two formats because a CRM migration left old records in US style,
that `order_ref` in payments is the order id under another name), which column is the
key, which files join, and which values are wrong. Then they hand-write staging models,
tests and a DAG.

That first pass usually takes days per source, and it is where pipelines go wrong
quietly: a type guessed from the first thousand rows, a duplicate key nobody noticed, a
status typo that becomes its own category in a dashboard.

## Why it is hard

Inferring a schema is not the hard part; pandas and DuckDB both guess types on read.
The hard part is that the guesses must be **checkable**. The generated code will run every
night on data nobody has fully seen, so every type, key and rule has to come with the
evidence for it, be reproducible from the same files, and be reviewable by a person
before it becomes the contract. And rules learned from the data inherit the data's
problems: a defect that is common enough looks normal.

## The approach

Three deterministic agents run as a state machine: **discovery** infers types (from
parse rates over the values), keys (null and distinct rates) and relationships (name
tokens plus the share of child rows that resolve in the parent key); **validation**
profiles each column and writes data-quality rules as Great Expectations-style YAML, each
with the evidence that produced it; the **builder** compiles the same rules into dbt SQL
(a `__checked` model that records which rules each row fails, a clean `stg_` model, a
`__rejected` quarantine), derives marts from the relationship graph, writes `schema.yml`
tests and an Airflow DAG, and runs `dbt build` on DuckDB.

The key decision: **the first run's rules are a draft contract, not the answer.** Later
drops are checked against the reviewed `rules.yml` (`--contract`) instead of re-learning
rules from each drop, because re-learning on a dirty drop teaches the rules that the dirt
is normal (measured below). A local model (Ollama) can optionally word column
descriptions; it never touches a type, key, rule or line of SQL. See
[ADR-0001](docs/adr/0001-deterministic-agents-reviewed-contract.md).

## The result

Full-size drop: 7 files, 1.86M rows, 45,677 injected defects (0.5% of rows per defect
type). Every number is from `make e2e`, captured in [outputs/report.md](outputs/report.md)
and [outputs/metrics.json](outputs/metrics.json).

**Schema inference against the answer key**

| | column types correct | primary keys | foreign keys |
|---|---|---|---|
| pandas default readers | 37/54 (68.5%) | n/a | n/a |
| DuckDB auto-detect | 44/54 (81.5%) | n/a | n/a |
| discovery agent | 54/54 (100%) | 7/7 | 7/7, 0 spurious, renamed `order_ref` included |

**Defects caught vs injected** (recall), and how many rows each rule set flags (precision)

| rule set | recall | rows flagged | row precision |
|---|---|---|---|
| hand-written first pass (every column required, first column unique) | 29.5% | 934,316 | 3.4% |
| validation agent, frequency-only categories | 83.8% | 38,934 | 98.3% |
| validation agent | **88.6%** | 41,122 | **98.4%** |

Split by defect, the blended number hides two different results. Duplicates, missing
required values, orphan foreign keys, type corruption, negative amounts, future dates and
malformed emails are caught 100%. **Status and payment-method typos: 63.4%.** **Prices
inflated 100x: 22.8%.** Of the 669 false alarms, 633 come from two rules: 322 orders with the rare
but real status `disputed` (0.08% of orders) are quarantined as typos, and 311 genuine
bulk order lines trip the quantity outlier warning.

**dbt build**: 31 models built, 91 tests pass, 5 warn, 0 fail. The dbt SQL quarantined
39,666 rows and the pandas runner blocked the same 39,666, row for row. Raw folder to
green build in 100 s on a laptop (discover 39 s, validate 44 s, land 6 s, dbt 11 s),
producing 136 rules, 39 dbt files (1,340 lines) and an 88-line DAG.

**The run that embarrasses the headline** ([outputs/stress.md](outputs/stress.md), 10%-scale drops)

| defect rate per type | 0.5% | 2% | 5% | 10% |
|---|---|---|---|---|
| rules re-learned from the drop: recall | 93.0% | **20.8%** | 16.3% | 16.3% |
| primary keys found | 7/7 | 4/7 | 4/7 | 4/7 |
| rules frozen from last month's drop: recall | 92.0% | 91.3% | 91.6% | 91.5% |

Re-learned rules fall off a cliff at 2%, not gradually. Every tolerance in the profiler
sits at about 1% (a column with at most 1% nulls is required, a key must be 99% distinct,
a number 99% non-negative), so once a defect passes 1% the rule that would catch it is
never written. The frozen contract holds recall flat. This is the reason `--contract`
exists and the reason the README leads with "reviewed".

A second stress arm re-encodes three columns in formats the parser does not know
(`15.03.2024`, `1.234,50`, `2024-01-01T08:02:50+04:00`). All three fall back to text
(type accuracy 94.4%) **without a warning**. Under a frozen contract the same change trips
the type rule on every row and the quarantine gate halts the run, which is the right
outcome, but on a first run nothing flags it.

**Description model** (optional, [outputs/llm_descriptions.json](outputs/llm_descriptions.json)):
llama3.2 3B on CPU wrote leads for 28 of 54 columns; 26 were rejected (unparseable or
missing) and fell back to rule text. 503 s for seven tables; the whole deterministic build step
takes 0.14 s. It is off by default.

## How it works

```
 raw/ (csv, json, jsonl, parquet)
   │
   ▼
 DISCOVER ──► catalog.yml      types + formats, keys, relationships, with rates
   │
   ▼
 VALIDATE ──► rules.yml        136 expectations, each with its evidence
   │      └─► quarantine rate per table ── > 5% ──► HALTED (nothing built)
   ▼
 BUILD ────► dbt/              sources, stg_*__checked, stg_*, stg_*__rejected,
   │                           dim_*, fct_*, agg_*_daily, schema.yml tests
   │       └► airflow/dags/    land_* ─► validate_drop ─► dbt_build
   ▼
 LAND ─────► warehouse.duckdb  raw.* as text, with file row position
   │
   ▼
 DBT_BUILD ► DONE / FAILED     retried once; results read from run_results.json
```

The transition table in `orchestrator.py` holds the order, the stop conditions and the
retry policy; `run_state.json` records every state, attempt and duration.
[docs/aws-mapping.md](docs/aws-mapping.md) maps each state onto Step Functions, Lambda
and SQS.

Inference rules, in brief. A column is type T if 98% of its non-empty values parse as T,
so 0.5% corruption does not demote an integer column to text. Codes with leading zeros
(`01234`) and identifiers like `SKU-000123` are kept as text. Dates must match one of a
listed set of formats; the formats found are recorded and compiled into
`try_strptime(..., [formats])`. A key is the most key-like name among columns with at most
1% nulls and 99% distinct values, else the best two-column combination. A foreign key needs
a name match (exact, or a shared token such as `order` in `order_ref`) and 90% of child
rows resolving in the parent key. Categorical columns drop values under 0.1% of rows and
values one or two edits away from a value ten times as common, which is what moves typo
recall from 9% to 63%.

The marts are decided by the relationship graph alone: tables referenced but referencing
nothing become `dim_`, tables that reference others become `fct_` with one hop of parent
context (order lines carry the order's timestamp, customer and store), and each fact with
its own date gets a daily aggregate. Joins are left joins, and the relationship tests that
can fail because of quarantine are at warn severity, so the cascade is counted instead of
hidden: quarantining 2.9% of orders leaves 22,619 order lines whose order is missing.

Generated output from the full-size run is kept in [examples/](examples/) so it can be read
without running anything.

## Running it

```bash
uv sync
make e2e        # generate the 1.86M-row drop, then discover, validate, build, dbt build, score
make stress     # defect-rate sweep, frozen-contract arm and format-shift arm
make check      # ruff, ruff format, mypy, pytest

# your own folder; later drops enforce the reviewed contract from a previous run
uv run pipeline-builder run --raw path/to/raw --out generated
uv run pipeline-builder run --raw path/to/next_drop --out generated_next --contract generated

# optional model-written column leads (needs `ollama serve` and `ollama pull llama3.2`)
uv run pipeline-builder run --raw data/raw --out generated --llm llama3.2

docker build -t pipeline-builder . && docker run --rm pipeline-builder
```

Outputs: `generated/catalog.yml`, `generated/rules.yml`, `generated/dbt/`,
`generated/airflow/dags/raw_drop_pipeline.py`, `generated/warehouse.duckdb`,
`generated/run_state.json`, and the scored report in `outputs/`.

## The data

The dataset is generated by the seeded script `scripts/generate_data.py` and sized to the
production problem: a regional retailer's nightly drop of 60k customers, 400k orders,
973k order lines, 405k payments, 20k returns, 4,000 products and 120 stores; production
data of this kind is proprietary. Orders follow weekly and annual seasonality with
Ramadan, back-to-school, Black Friday and year-end peaks; prices are lognormal by
category; customer frequency is heavy-tailed; and the files carry the mess real exports
do: two date formats in one column, prices with `$` and thousands separators, booleans
spelled `Y/N`, `true/false` and `1/0`, empty optional fields, a nested JSON object, a
renamed foreign key, postal codes with leading zeros, a rare legitimate status and
legitimate bulk orders.

Nine defect types are injected at known rows, so the answer key (`data/ground_truth/`)
holds every type, key, relationship and defect. The limit: the generator and the
inference rules were written by the same person, so 100% type accuracy on this drop is an
upper bound, not evidence that the rules generalise. The format-shift arm is the honest
measure of that, and it fails.

## Tests

`tests/test_evaluate.py` runs the scorer against deliberately broken inputs first (no
flags, right row on the wrong column, a duplicate caught by a non-uniqueness rule,
everything flagged) so the recall and precision numbers can be trusted before any agent
is scored.

`tests/test_pipeline.py::test_dbt_quarantine_matches_pandas_rule_runner_row_for_row`
builds the generated dbt project on a small drop and requires the SQL quarantine and the
pandas rule runner to reject exactly the same rows. This is what makes the scored numbers
(computed in pandas) a statement about the SQL that would run in production.
`test_frozen_contract_still_catches_defects_on_a_much_dirtier_drop` pins the stress
finding: on a 5% drop, re-learned rules catch none of the missing required values and the
frozen contract catches all of them.

Other tests cover the parsers' traps, inference against the answer key, the gate halting
before anything is built, model output that is not JSON, and the generated DAG executed
against stub Airflow modules to check task order. 25 tests, no model server, no network.

## Known limitations

- **Biggest gap: a first run on a source with more than about 1% of any defect writes
  wrong rules without saying so.** The quarantine gate catches large cases and the frozen
  contract fixes later runs, but the first contract still needs a person to read
  `rules.yml`. The obvious typos it accepts (`cancel`, `pending_x`, `paypal_x`) are
  visible there in the `value_set` lists.
- Inflated prices are mostly missed (22.8%). The outlier rule is per column; catching a
  100x price on a cheap item needs a rule relative to that product's list price, which
  means a cross-table rule the validator does not generate.
- A rare legitimate category is quarantined as a typo (`disputed`, 322 orders). Frequency
  cannot separate it from a repeated typo; only a person or a source system's code list can.
- Parser vocabulary is fixed. European dates, decimal commas and UTC offsets become text
  on a first run.
- Mart design is structural only. The builder does not know which measures matter, so the
  daily aggregates sum every decimal column, including ones nobody would add up.
- The Airflow DAG is generated and checked against stub modules, not run on a live Airflow.
  It uses Airflow 2 import paths.
- The Docker image is defined but was not built on this machine (the local Docker daemon
  failed with an I/O error); CI does not build it either.
- The description model's quality was not scored; only how many of its outputs were usable.

## Insights and learning

I picked this because the slow part of onboarding a source is not writing SQL, it is the
detective work before it, and that work is usually lost: the person who figured out that
`order_ref` means order id leaves it in their head or a Slack thread. Writing it down as a
catalog and a rule file with evidence was the point; generating the dbt code from it is
almost mechanical once that exists.

What surprised me was the shape of the failure in the stress run. I expected recall to
degrade as the defect rate rose. It did not degrade, it collapsed, between 0.5% and 2%,
because every threshold I had picked was near 1%. Thresholds that are reasonable one at a
time fail together. That changed the design: the most valuable feature turned out to be the
`--contract` flag, which is a few lines of code, not any of the inference.

What I got wrong first: the decimal regex accepted any three capital letters as a currency
code, so `SKU-000123` parsed as a negative amount in a currency called SKU and every SKU
column was typed as decimal. Foreign-key containment measured on distinct values also
undercounted badly, because a few unique orphan ids outweighed thousands of rows that
resolved. Both were found by scoring against the answer key, not by looking at the output.
And the first status rule accepted every typo, because each typo was frequent enough to
look like a real category (recall 9%); the edit-distance check was added after that.

Next I would add cross-table rules (a line price relative to its product's list price),
make the parser vocabulary data-driven so an unknown date format raises a question rather
than silently becoming text, and let the reviewed contract record why a rule was edited so
the next run can explain itself.

## License

MIT
