-- Rows of order_items.parquet quarantined by at least one blocking rule, with the raw text kept.
select
    _row_id,
    failed_rules,
    order_id__text as order_id,
    line_number__text as line_number,
    sku__text as sku,
    quantity__text as quantity,
    unit_price__text as unit_price,
    discount_pct__text as discount_pct
from {{ ref('stg_order_items__checked') }}
where len(failed_rules) > 0
