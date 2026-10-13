-- Rows of orders.csv that pass every blocking rule, typed and standardised.
select
    _row_id,
    order_id,
    customer_id,
    store_id,
    ordered_at,
    lower(status) as status,
    lower(channel) as channel,
    order_total,
    lower(discount_code) as discount_code,
    is_gift,
    len(warned_rules) > 0 as has_warnings
from {{ ref('stg_orders__checked') }}
where len(failed_rules) = 0
