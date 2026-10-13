{{ config(materialized='table') }}

with source as (
    select * from {{ source('raw', 'returns') }}
),

cleaned as (
    select
        _row_id,
        {{ clean_text('return_id') }} as return_id__text,
        {{ clean_text('order_id') }} as order_id__text,
        {{ clean_text('sku') }} as sku__text,
        {{ clean_text('returned_on') }} as returned_on__text,
        {{ clean_text('reason') }} as reason__text,
        {{ clean_text('refund_amount') }} as refund_amount__text
    from source
),

typed as (
    select
        *,
        case when regexp_full_match(return_id__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(return_id__text, ',', '') as bigint) end as return_id,
        case when regexp_full_match(order_id__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(order_id__text, ',', '') as bigint) end as order_id,
        sku__text as sku,
        cast(try_strptime(returned_on__text, ['%d %b %Y']) as date) as returned_on,
        reason__text as reason,
        case when regexp_full_match(refund_amount__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)?[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$') then try_cast(regexp_replace(refund_amount__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)|,', '', 'g') as decimal(18, 4)) end as refund_amount,
        case when return_id__text is not null then row_number() over (partition by return_id__text order by _row_id) else 1 end as _key_seq
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
            case when _key_seq > 1 then 'returns__pk__unique' end,
            case when return_id__text is null then 'returns__return_id__not_null' end,
            case when (return_id__text is not null and return_id is null) then 'returns__return_id__type' end,
            case when order_id__text is null then 'returns__order_id__not_null' end,
            case when (order_id__text is not null and order_id is null) then 'returns__order_id__type' end,
            case when sku__text is null then 'returns__sku__not_null' end,
            case when (sku__text is not null and not regexp_full_match(sku__text, '^[A-Z]{3}\-\d{6}$')) then 'returns__sku__pattern' end,
            case when returned_on__text is null then 'returns__returned_on__not_null' end,
            case when (returned_on__text is not null and returned_on is null) then 'returns__returned_on__type' end,
            case when coalesce(returned_on < timestamp '1990-01-01' or returned_on >= timestamp '2026-02-01' + interval 1 day, false) then 'returns__returned_on__window' end,
            case when (reason__text is not null and lower(reason__text) not in ('changed_mind', 'damaged', 'late_delivery', 'not_as_described', 'wrong_size')) then 'returns__reason__in_set' end,
            case when refund_amount__text is null then 'returns__refund_amount__not_null' end,
            case when (refund_amount__text is not null and refund_amount is null) then 'returns__refund_amount__type' end,
            case when coalesce(refund_amount < 0, false) then 'returns__refund_amount__non_negative' end,
            case when (order_id is not null and parent_0.k is null) then 'returns__order_id__references_orders' end,
            case when (sku is not null and parent_1.k is null) then 'returns__sku__references_products' end
        ], x -> x is not null) as failed_rules,
        list_filter([
            case when coalesce(refund_amount > 74397.81, false) then 'returns__refund_amount__outlier' end
        ], x -> x is not null) as warned_rules
    from typed
    left join parent_0 on parent_0.k = typed.order_id
    left join parent_1 on parent_1.k = typed.sku
)

select * from flagged
