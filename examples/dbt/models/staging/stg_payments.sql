-- Rows of payments.csv that pass every blocking rule, typed and standardised.
select
    _row_id,
    payment_id,
    order_ref,
    paid_at,
    lower(method) as method,
    amount,
    lower(currency) as currency,
    is_settled,
    len(warned_rules) > 0 as has_warnings
from {{ ref('stg_payments__checked') }}
where len(failed_rules) = 0
