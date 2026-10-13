-- Rows of stores.csv that pass every blocking rule, typed and standardised.
select
    _row_id,
    store_id,
    store_name,
    lower(region) as region,
    opened_on,
    is_online,
    square_meters,
    len(warned_rules) > 0 as has_warnings
from {{ ref('stg_stores__checked') }}
where len(failed_rules) = 0
