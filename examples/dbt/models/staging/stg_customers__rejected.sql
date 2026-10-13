-- Rows of customers.csv quarantined by at least one blocking rule, with the raw text kept.
select
    _row_id,
    failed_rules,
    customer_id__text as customer_id,
    email__text as email,
    first_name__text as first_name,
    last_name__text as last_name,
    signup_date__text as signup_date,
    country__text as country,
    loyalty_tier__text as loyalty_tier,
    phone__text as phone,
    postal_code__text as postal_code,
    marketing_opt_in__text as marketing_opt_in,
    birth_year__text as birth_year
from {{ ref('stg_customers__checked') }}
where len(failed_rules) > 0
