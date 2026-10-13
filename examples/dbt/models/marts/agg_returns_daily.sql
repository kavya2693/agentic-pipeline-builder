{{ config(materialized='table') }}

select
    cast(returned_on as date) as day,
    count(*) as rows,
    sum(refund_amount) as refund_amount
from {{ ref('stg_returns') }}
group by 1
order by 1
