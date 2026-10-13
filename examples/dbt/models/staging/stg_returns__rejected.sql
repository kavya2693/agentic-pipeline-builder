-- Rows of returns.jsonl quarantined by at least one blocking rule, with the raw text kept.
select
    _row_id,
    failed_rules,
    return_id__text as return_id,
    order_id__text as order_id,
    sku__text as sku,
    returned_on__text as returned_on,
    reason__text as reason,
    refund_amount__text as refund_amount
from {{ ref('stg_returns__checked') }}
where len(failed_rules) > 0
