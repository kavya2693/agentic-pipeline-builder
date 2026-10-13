-- Rows of products.json quarantined by at least one blocking rule, with the raw text kept.
select
    _row_id,
    failed_rules,
    sku__text as sku,
    product_name__text as product_name,
    category__text as category,
    list_price__text as list_price,
    launched_on__text as launched_on,
    is_active__text as is_active,
    weight_kg__text as weight_kg,
    attributes_brand__text as attributes_brand,
    attributes_color__text as attributes_color
from {{ ref('stg_products__checked') }}
where len(failed_rules) > 0
