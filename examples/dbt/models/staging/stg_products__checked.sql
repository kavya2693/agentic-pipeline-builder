{{ config(materialized='table') }}

with source as (
    select * from {{ source('raw', 'products') }}
),

cleaned as (
    select
        _row_id,
        {{ clean_text('sku') }} as sku__text,
        {{ clean_text('product_name') }} as product_name__text,
        {{ clean_text('category') }} as category__text,
        {{ clean_text('list_price') }} as list_price__text,
        {{ clean_text('launched_on') }} as launched_on__text,
        {{ clean_text('is_active') }} as is_active__text,
        {{ clean_text('weight_kg') }} as weight_kg__text,
        {{ clean_text('attributes_brand') }} as attributes_brand__text,
        {{ clean_text('attributes_color') }} as attributes_color__text
    from source
),

typed as (
    select
        *,
        sku__text as sku,
        product_name__text as product_name,
        category__text as category,
        case when regexp_full_match(list_price__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)?[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$') then try_cast(regexp_replace(list_price__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)|,', '', 'g') as decimal(18, 4)) end as list_price,
        cast(try_strptime(launched_on__text, ['%Y-%m-%d']) as date) as launched_on,
        case when lower(is_active__text) in ('1', 't', 'true', 'y', 'yes') then true when lower(is_active__text) in ('0', 'f', 'false', 'n', 'no') then false end as is_active,
        case when regexp_full_match(weight_kg__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)?[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$') then try_cast(regexp_replace(weight_kg__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)|,', '', 'g') as decimal(18, 4)) end as weight_kg,
        attributes_brand__text as attributes_brand,
        attributes_color__text as attributes_color,
        case when sku__text is not null then row_number() over (partition by sku__text order by _row_id) else 1 end as _key_seq
    from cleaned
),

flagged as (
    select
        typed.*,
        list_filter([
            case when _key_seq > 1 then 'products__pk__unique' end,
            case when sku__text is null then 'products__sku__not_null' end,
            case when (sku__text is not null and not regexp_full_match(sku__text, '^[A-Z]{3}\-\d{6}$')) then 'products__sku__pattern' end,
            case when product_name__text is null then 'products__product_name__not_null' end,
            case when category__text is null then 'products__category__not_null' end,
            case when (category__text is not null and lower(category__text) not in ('apparel', 'beauty', 'electronics', 'grocery', 'home', 'household', 'sports', 'toys')) then 'products__category__in_set' end,
            case when list_price__text is null then 'products__list_price__not_null' end,
            case when (list_price__text is not null and list_price is null) then 'products__list_price__type' end,
            case when coalesce(list_price < 0, false) then 'products__list_price__non_negative' end,
            case when launched_on__text is null then 'products__launched_on__not_null' end,
            case when (launched_on__text is not null and launched_on is null) then 'products__launched_on__type' end,
            case when coalesce(launched_on < timestamp '1990-01-01' or launched_on >= timestamp '2026-02-01' + interval 1 day, false) then 'products__launched_on__window' end,
            case when is_active__text is null then 'products__is_active__not_null' end,
            case when (is_active__text is not null and is_active is null) then 'products__is_active__type' end,
            case when (weight_kg__text is not null and weight_kg is null) then 'products__weight_kg__type' end,
            case when coalesce(weight_kg < 0, false) then 'products__weight_kg__non_negative' end,
            case when attributes_brand__text is null then 'products__attributes_brand__not_null' end,
            case when (attributes_brand__text is not null and lower(attributes_brand__text) not in ('aster', 'borealis', 'cedar', 'dune', 'ember', 'falcon', 'garnet', 'harbor', 'iris', 'juniper')) then 'products__attributes_brand__in_set' end,
            case when (attributes_color__text is not null and lower(attributes_color__text) not in ('beige', 'black', 'blue', 'green', 'grey', 'red', 'white')) then 'products__attributes_color__in_set' end
        ], x -> x is not null) as failed_rules,
        list_filter([
            case when coalesce(list_price > 23634.019, false) then 'products__list_price__outlier' end,
            case when coalesce(weight_kg > 103.7345, false) then 'products__weight_kg__outlier' end
        ], x -> x is not null) as warned_rules
    from typed
)

select * from flagged
