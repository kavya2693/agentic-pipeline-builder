{{ config(materialized='table') }}

select
    cast(ordered_at as date) as day,
    count(*) as rows,
    sum(order_total) as order_total
from {{ ref('stg_orders') }}
group by 1
order by 1
