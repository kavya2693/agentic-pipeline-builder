{{ config(materialized='table') }}

with source as (
    select * from {{ source('raw', 'orders') }}
),

cleaned as (
    select
        _row_id,
        {{ clean_text('order_id') }} as order_id__text,
        {{ clean_text('customer_id') }} as customer_id__text,
        {{ clean_text('store_id') }} as store_id__text,
        {{ clean_text('ordered_at') }} as ordered_at__text,
        {{ clean_text('status') }} as status__text,
        {{ clean_text('channel') }} as channel__text,
        {{ clean_text('order_total') }} as order_total__text,
        {{ clean_text('discount_code') }} as discount_code__text,
        {{ clean_text('is_gift') }} as is_gift__text
    from source
),

typed as (
    select
        *,
        case when regexp_full_match(order_id__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(order_id__text, ',', '') as bigint) end as order_id,
        case when regexp_full_match(customer_id__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(customer_id__text, ',', '') as bigint) end as customer_id,
        store_id__text as store_id,
        cast(try_strptime(ordered_at__text, ['%Y-%m-%d %H:%M:%S', '%m/%d/%Y %H:%M']) as timestamp) as ordered_at,
        status__text as status,
        channel__text as channel,
        case when regexp_full_match(order_total__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)?[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$') then try_cast(regexp_replace(order_total__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)|,', '', 'g') as decimal(18, 4)) end as order_total,
        discount_code__text as discount_code,
        case when lower(is_gift__text) in ('1', 't', 'true', 'y', 'yes') then true when lower(is_gift__text) in ('0', 'f', 'false', 'n', 'no') then false end as is_gift,
        case when order_id__text is not null then row_number() over (partition by order_id__text order by _row_id) else 1 end as _key_seq
    from cleaned
),

parent_0 as (
    select distinct case when regexp_full_match({{ clean_text('customer_id') }}, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace({{ clean_text('customer_id') }}, ',', '') as bigint) end as k
    from {{ source('raw', 'customers') }}
),

parent_1 as (
    select distinct {{ clean_text('store_id') }} as k
    from {{ source('raw', 'stores') }}
),

flagged as (
    select
        typed.*,
        list_filter([
            case when _key_seq > 1 then 'orders__pk__unique' end,
            case when order_id__text is null then 'orders__order_id__not_null' end,
            case when (order_id__text is not null and order_id is null) then 'orders__order_id__type' end,
            case when customer_id__text is null then 'orders__customer_id__not_null' end,
            case when (customer_id__text is not null and customer_id is null) then 'orders__customer_id__type' end,
            case when store_id__text is null then 'orders__store_id__not_null' end,
            case when (store_id__text is not null and not regexp_full_match(store_id__text, '^[A-Z]{2}\-\d{4}$')) then 'orders__store_id__pattern' end,
            case when ordered_at__text is null then 'orders__ordered_at__not_null' end,
            case when (ordered_at__text is not null and ordered_at is null) then 'orders__ordered_at__type' end,
            case when coalesce(ordered_at < timestamp '1990-01-01' or ordered_at >= timestamp '2026-02-01' + interval 1 day, false) then 'orders__ordered_at__window' end,
            case when status__text is null then 'orders__status__not_null' end,
            case when (status__text is not null and lower(status__text) not in ('cancel', 'cancelled', 'delivered', 'pending_x', 'placed', 'returned', 'shipped')) then 'orders__status__in_set' end,
            case when channel__text is null then 'orders__channel__not_null' end,
            case when (channel__text is not null and lower(channel__text) not in ('app', 'store', 'web')) then 'orders__channel__in_set' end,
            case when order_total__text is null then 'orders__order_total__not_null' end,
            case when (order_total__text is not null and order_total is null) then 'orders__order_total__type' end,
            case when coalesce(order_total < 0, false) then 'orders__order_total__non_negative' end,
            case when (discount_code__text is not null and lower(discount_code__text) not in ('bf25', 'freeship', 'ramadan15', 'vip20', 'welcome10')) then 'orders__discount_code__in_set' end,
            case when is_gift__text is null then 'orders__is_gift__not_null' end,
            case when (is_gift__text is not null and is_gift is null) then 'orders__is_gift__type' end,
            case when (customer_id is not null and parent_0.k is null) then 'orders__customer_id__references_customers' end,
            case when (store_id is not null and parent_1.k is null) then 'orders__store_id__references_stores' end
        ], x -> x is not null) as failed_rules,
        list_filter([
            case when coalesce(order_total > 124461.693, false) then 'orders__order_total__outlier' end
        ], x -> x is not null) as warned_rules
    from typed
    left join parent_0 on parent_0.k = typed.customer_id
    left join parent_1 on parent_1.k = typed.store_id
)

select * from flagged
