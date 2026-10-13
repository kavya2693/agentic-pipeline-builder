{{ config(materialized='table') }}

select
    f.*,
    p0.customer_id,
    p0.store_id,
    p0.ordered_at
from {{ ref('stg_payments') }} as f
left join {{ ref('stg_orders') }} as p0 on p0.order_id = f.order_ref
