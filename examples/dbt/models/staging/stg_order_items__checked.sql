{{ config(materialized='table') }}

with source as (
    select * from {{ source('raw', 'order_items') }}
),

cleaned as (
    select
        _row_id,
        {{ clean_text('order_id') }} as order_id__text,
        {{ clean_text('line_number') }} as line_number__text,
        {{ clean_text('sku') }} as sku__text,
        {{ clean_text('quantity') }} as quantity__text,
        {{ clean_text('unit_price') }} as unit_price__text,
        {{ clean_text('discount_pct') }} as discount_pct__text
    from source
),

typed as (
    select
        *,
        case when regexp_full_match(order_id__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(order_id__text, ',', '') as bigint) end as order_id,
        case when regexp_full_match(line_number__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(line_number__text, ',', '') as bigint) end as line_number,
        sku__text as sku,
        case when regexp_full_match(quantity__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(quantity__text, ',', '') as bigint) end as quantity,
        case when regexp_full_match(unit_price__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)?[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$') then try_cast(regexp_replace(unit_price__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)|,', '', 'g') as decimal(18, 4)) end as unit_price,
        case when regexp_full_match(discount_pct__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)?[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$') then try_cast(regexp_replace(discount_pct__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)|,', '', 'g') as decimal(18, 4)) end as discount_pct,
        case when order_id__text is not null and line_number__text is not null then row_number() over (partition by order_id__text, line_number__text order by _row_id) else 1 end as _key_seq
    from cleaned
),

parent_0 as (
    select distinct case when regexp_full_match({{ clean_text('order_id') }}, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace({{ clean_text('order_id') }}, ',', '') as bigint) end as k
    from {{ source('raw', 'orders') }}
),

parent_1 as (
    select distinct {{ clean_text('sku') }} as k
    from {{ source('raw', 'products') }}
),

flagged as (
    select
        typed.*,
        list_filter([
            case when _key_seq > 1 then 'order_items__pk__unique' end,
            case when order_id__text is null then 'order_items__order_id__not_null' end,
            case when (order_id__text is not null and order_id is null) then 'order_items__order_id__type' end,
            case when line_number__text is null then 'order_items__line_number__not_null' end,
            case when (line_number__text is not null and line_number is null) then 'order_items__line_number__type' end,
            case when sku__text is null then 'order_items__sku__not_null' end,
            case when (sku__text is not null and not regexp_full_match(sku__text, '^[A-Z]{3}\-\d{6}$')) then 'order_items__sku__pattern' end,
            case when quantity__text is null then 'order_items__quantity__not_null' end,
            case when (quantity__text is not null and quantity is null) then 'order_items__quantity__type' end,
            case when coalesce(quantity < 0, false) then 'order_items__quantity__non_negative' end,
            case when unit_price__text is null then 'order_items__unit_price__not_null' end,
            case when (unit_price__text is not null and unit_price is null) then 'order_items__unit_price__type' end,
            case when coalesce(unit_price < 0, false) then 'order_items__unit_price__non_negative' end,
            case when discount_pct__text is null then 'order_items__discount_pct__not_null' end,
            case when (discount_pct__text is not null and discount_pct is null) then 'order_items__discount_pct__type' end,
            case when coalesce(discount_pct < 0, false) then 'order_items__discount_pct__non_negative' end,
            case when (order_id is not null and parent_0.k is null) then 'order_items__order_id__references_orders' end,
            case when (sku is not null and parent_1.k is null) then 'order_items__sku__references_products' end
        ], x -> x is not null) as failed_rules,
        list_filter([
            case when coalesce(quantity > 60.0, false) then 'order_items__quantity__outlier' end,
            case when coalesce(unit_price > 27672.612, false) then 'order_items__unit_price__outlier' end,
            case when coalesce(discount_pct > 2.5, false) then 'order_items__discount_pct__outlier' end
        ], x -> x is not null) as warned_rules
    from typed
    left join parent_0 on parent_0.k = typed.order_id
    left join parent_1 on parent_1.k = typed.sku
)

select * from flagged
