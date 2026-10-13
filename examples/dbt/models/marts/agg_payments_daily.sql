{{ config(materialized='table') }}

select
    cast(paid_at as date) as day,
    count(*) as rows,
    sum(amount) as amount
from {{ ref('stg_payments') }}
group by 1
order by 1
