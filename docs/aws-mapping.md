# Mapping the run onto AWS

The orchestrator in `src/pipeline_builder/orchestrator.py` is a transition table, which
is the same shape as an Amazon States Language definition. Nothing here is deployed; this
is how the local state machine would move to Step Functions, Lambda and SQS.

| Local state | AWS | Notes |
|---|---|---|
| file arrives in `data/raw/` | S3 `ObjectCreated` event to an SQS queue | the queue batches a nightly drop into one execution and absorbs retries from the producer |
| `DISCOVER` | Step Functions `Map` state, one Lambda per file, then a reduce Lambda | per-file type and key inference is independent; relationships need all files, so they run in the reduce step |
| `VALIDATE` | Lambda (or ECS task for files over Lambda's 10 GB memory) | writes `rules.yml` and the per-table quarantine summary to S3 |
| gate `too_many_rejects` -> `HALTED` | `Choice` state to a `Task` with `.waitForTaskToken` | pauses the execution until a person approves or edits the contract; the token is sent by SNS email or a Slack hook |
| `BUILD` | Lambda | writes the dbt project and DAG to S3 or commits them to the repo |
| `LAND` | Lambda per file, or Athena `CREATE TABLE AS` over S3 | DuckDB's single-writer limit goes away; landing runs in parallel |
| `DBT_BUILD` | ECS Fargate task running `dbt build` (dbt-athena or dbt-redshift adapter instead of dbt-duckdb) | retried twice, as `MAX_ATTEMPTS` does locally, with `Retry` on `States.TaskFailed` |
| `run_state.json` | the execution history | each state's input, output and duration are kept by Step Functions |
| `FAILED` | `Fail` state plus an EventBridge rule to alert | |

What changes when moving: the SQL dialect (DuckDB's `try_strptime` and `regexp_full_match`
become `try(date_parse(...))` and `regexp_like` on Athena), and the warehouse adapter in
`profiles.yml`. The discovery and validation code is plain pandas and moves unchanged.
