# Run report

Final state: **DONE**

## State log

| state | attempt | event | seconds |
|---|---|---|---|
| DISCOVER | 1 | ok | 38.67 |
| VALIDATE | 1 | ok | 44.15 |
| BUILD | 1 | ok | 0.14 |
| LAND | 1 | ok | 5.93 |
| DBT_BUILD | 1 | ok | 10.99 |
| DONE | 1 | end | 0.0 |

## Schema inference vs answer key

| method | columns typed correctly |
|---|---|
| agent | 54/54 (100.0%) |
| pandas_default | 37/54 (68.5%) |
| duckdb_sniff | 44/54 (81.5%) |

Primary keys: 7/7. Foreign keys: 7/7 found, 0 spurious (precision 100%, recall 100%).

## Defects caught vs injected

| rule set | recall | flagged rows | row precision |
|---|---|---|---|
| agent | 88.6% | 41,122 | 98.4% |
| naive_baseline | 29.5% | 934,316 | 3.4% |
| agent_without_typo_check | 83.8% | 38,934 | 98.3% |

| defect | injected | agent caught | recall |
|---|---|---|---|
| duplicate_row | 4,316 | 4,316 | 100.0% |
| future_date | 2,300 | 2,300 | 100.0% |
| invalid_enum | 4,016 | 2,546 | 63.4% |
| malformed_email | 300 | 300 | 100.0% |
| negative_value | 6,879 | 6,879 | 100.0% |
| null_required | 9,179 | 9,179 | 100.0% |
| orphan_fk | 6,961 | 6,961 | 100.0% |
| outlier_value | 4,863 | 1,108 | 22.8% |
| type_corruption | 6,863 | 6,863 | 100.0% |

| rule | rows flagged with no injected defect |
|---|---|
| orders__status__in_set | 322 |
| order_items__quantity__outlier | 311 |
| orders__order_total__outlier | 17 |
| payments__amount__outlier | 17 |
| returns__refund_amount__outlier | 2 |

## dbt build

Models: {'success': 31}. Tests: {'pass': 91, 'warn': 5}. Elapsed 9.3 s.

Warned tests (failing rows): {'relationships_stg_returns_order_id__order_id__ref_stg_orders_': 467, 'relationships_stg_orders_customer_id__customer_id__ref_stg_customers_': 6086, 'relationships_stg_order_items_order_id__order_id__ref_stg_orders_': 22619, 'relationships_stg_payments_order_ref__order_id__ref_stg_orders_': 9416, 'relationships_fct_orders_customer_id__customer_id__ref_dim_customer_': 6086}

| table | rejected by dbt SQL | blocked by pandas | both | only SQL | only pandas |
|---|---|---|---|---|---|
| customers | 1,200 | 1,200 | 1,200 | 0 | 0 |
| order_items | 19,452 | 19,452 | 19,452 | 0 | 0 |
| orders | 11,507 | 11,507 | 11,507 | 0 | 0 |
| payments | 7,409 | 7,409 | 7,409 | 0 | 0 |
| products | 0 | 0 | 0 | 0 | 0 |
| returns | 98 | 98 | 98 | 0 | 0 |
| stores | 0 | 0 | 0 | 0 | 0 |

Mart row counts: dim_customer 59,100, dim_product 4,000, dim_store 120, fct_order_items 953,193, fct_orders 390,493, fct_payments 397,725, fct_returns 19,477, agg_orders_daily 731, agg_payments_daily 733, agg_returns_daily 758
