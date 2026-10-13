{{ config(materialized='table') }}

select
    f.*
from {{ ref('stg_orders') }} as f
