-- Rows of stores.csv quarantined by at least one blocking rule, with the raw text kept.
select
    _row_id,
    failed_rules,
    store_id__text as store_id,
    store_name__text as store_name,
    region__text as region,
    opened_on__text as opened_on,
    is_online__text as is_online,
    square_meters__text as square_meters
from {{ ref('stg_stores__checked') }}
where len(failed_rules) > 0
