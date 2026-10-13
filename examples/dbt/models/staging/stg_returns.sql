-- Rows of returns.jsonl that pass every blocking rule, typed and standardised.
select
    _row_id,
    return_id,
    order_id,
    sku,
    returned_on,
    lower(reason) as reason,
    refund_amount,
    len(warned_rules) > 0 as has_warnings
from {{ ref('stg_returns__checked') }}
where len(failed_rules) = 0
