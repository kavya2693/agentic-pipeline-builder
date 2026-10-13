-- Rows of customers.csv that pass every blocking rule, typed and standardised.
select
    _row_id,
    customer_id,
    email,
    first_name,
    last_name,
    signup_date,
    lower(country) as country,
    lower(loyalty_tier) as loyalty_tier,
    phone,
    postal_code,
    marketing_opt_in,
    birth_year,
    len(warned_rules) > 0 as has_warnings
from {{ ref('stg_customers__checked') }}
where len(failed_rules) = 0
