-- Rows of orders.csv quarantined by at least one blocking rule, with the raw text kept.
select
    _row_id,
    failed_rules,
    order_id__text as order_id,
    customer_id__text as customer_id,
    store_id__text as store_id,
    ordered_at__text as ordered_at,
    status__text as status,
    channel__text as channel,
    order_total__text as order_total,
    discount_code__text as discount_code,
    is_gift__text as is_gift
from {{ ref('stg_orders__checked') }}
where len(failed_rules) > 0
