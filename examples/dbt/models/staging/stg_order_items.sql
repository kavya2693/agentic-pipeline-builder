-- Rows of order_items.parquet that pass every blocking rule, typed and standardised.
select
    _row_id,
    order_id,
    line_number,
    sku,
    quantity,
    unit_price,
    discount_pct,
    len(warned_rules) > 0 as has_warnings
from {{ ref('stg_order_items__checked') }}
where len(failed_rules) = 0
