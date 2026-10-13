-- Rows of products.json that pass every blocking rule, typed and standardised.
select
    _row_id,
    sku,
    product_name,
    lower(category) as category,
    list_price,
    launched_on,
    is_active,
    weight_kg,
    lower(attributes_brand) as attributes_brand,
    lower(attributes_color) as attributes_color,
    len(warned_rules) > 0 as has_warnings
from {{ ref('stg_products__checked') }}
where len(failed_rules) = 0
