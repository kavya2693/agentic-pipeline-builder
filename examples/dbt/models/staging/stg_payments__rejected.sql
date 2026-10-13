-- Rows of payments.csv quarantined by at least one blocking rule, with the raw text kept.
select
    _row_id,
    failed_rules,
    payment_id__text as payment_id,
    order_ref__text as order_ref,
    paid_at__text as paid_at,
    method__text as method,
    amount__text as amount,
    currency__text as currency,
    is_settled__text as is_settled
from {{ ref('stg_payments__checked') }}
where len(failed_rules) > 0
