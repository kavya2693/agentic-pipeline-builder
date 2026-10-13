# ADR-0001: Deterministic agents, rules learned once and frozen as a reviewed contract

## Status
Accepted

## Context
A new source system arrives as a folder of CSV, JSON and Parquet exports. Before an
engineer can write staging models, they spend days working out what each column really
is, which column is the key, which files join to which, and which values are wrong. The
goal is to shorten that, but the output is code that will run every night against data
nobody has fully seen, so it must be reproducible and reviewable line by line.

Three questions decide the design: who infers the schema (rules or a model), where the
data-quality rules come from (the data or a person), and what happens to a bad row.

## Decision
- **Discovery, validation and building are deterministic code.** Types come from parse
  rates over the values, keys from null and distinct rates, relationships from name
  tokens plus the share of child rows that resolve in the parent key. Every threshold is
  a named constant. A model is used only to word column descriptions, and the facts in
  each description (key, reference, allowed values) are appended by code after the
  model's phrase.
- **Rules are written as Great Expectations-style YAML with the evidence that produced
  each one**, so the first run's output is a draft contract a person edits.
- **Later runs enforce the reviewed contract** (`--contract`) instead of re-learning rules
  from each new drop. Re-learning on a dirty drop teaches the rules that the dirt is
  normal; the stress run shows recall falling from 93% to 21% when the defect rate goes
  from 0.5% to 2%, and staying at 91% when the contract is frozen.
- **Bad rows are quarantined, not dropped.** Each `stg_<table>__checked` model records the
  ids of the rules a row fails; `stg_<table>` keeps clean rows and `stg_<table>__rejected`
  keeps the rest with reasons.
- **The rules run twice, in pandas and in the generated SQL, and the two must agree row
  for row.** The pandas runner is what the scorer reads; the SQL is what production runs.
  A test fails if they disagree on a single row.
- **The run is a state machine** (discover, validate, build, land, dbt build) with a gate
  that halts before building if any table would quarantine more than 5% of its rows.

## Alternatives rejected
- **A model infers schemas and writes the SQL.** Tried as a thought experiment against
  the review requirement: the same files can produce different SQL on the next run, and a
  reviewer cannot tell a hallucinated join from a real one without redoing the discovery.
- **Great Expectations as a dependency.** Its profiler does the inference part, but the
  project needs the same rules compiled into dbt SQL; carrying both the GX runtime and a
  translation layer was more code than the nine expectations used here.
- **Drop bad rows in staging.** Simpler, but loses the evidence; the quarantine table is
  what lets a source owner be told exactly which rows to fix.
- **Inner joins in marts.** Rejected because a quarantined customer would silently remove
  that customer's orders. Marts use left joins and a relationship test at warn severity,
  so the cascade is counted instead of hidden.

## Consequences
- CI runs the whole thing, including `dbt build` on DuckDB, with no model server or key.
- The first run on a source with more than about 1% of any defect produces wrong rules
  without saying so. The gate catches the large cases; the reviewed contract is the
  real fix, and it needs a person.
- Formats outside the parser vocabulary (European dates, decimal commas, UTC offsets)
  fall back to text. The vocabulary is a list in `typing_rules.py` and has to grow by hand.
