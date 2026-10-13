{{ config(materialized='table') }}

with source as (
    select * from {{ source('raw', 'payments') }}
),

cleaned as (
    select
        _row_id,
        {{ clean_text('payment_id') }} as payment_id__text,
        {{ clean_text('order_ref') }} as order_ref__text,
        {{ clean_text('paid_at') }} as paid_at__text,
        {{ clean_text('method') }} as method__text,
        {{ clean_text('amount') }} as amount__text,
        {{ clean_text('currency') }} as currency__text,
        {{ clean_text('is_settled') }} as is_settled__text
    from source
),

typed as (
    select
        *,
        payment_id__text as payment_id,
        case when regexp_full_match(order_ref__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(order_ref__text, ',', '') as bigint) end as order_ref,
        cast(try_strptime(paid_at__text, ['%Y-%m-%dT%H:%M:%S']) as timestamp) as paid_at,
        method__text as method,
        case when regexp_full_match(amount__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)?[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$') then try_cast(regexp_replace(amount__text, '^(?:(?:AED|SAR|USD|EUR|GBP|INR|QAR|OMR|KWD|BHD)\s?|\$)|,', '', 'g') as decimal(18, 4)) end as amount,
        currency__text as currency,
        case when lower(is_settled__text) in ('1', 't', 'true', 'y', 'yes') then true when lower(is_settled__text) in ('0', 'f', 'false', 'n', 'no') then false end as is_settled,
        case when payment_id__text is not null then row_number() over (partition by payment_id__text order by _row_id) else 1 end as _key_seq
    from cleaned
),

parent_0 as (
    select distinct case when regexp_full_match({{ clean_text('order_id') }}, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace({{ clean_text('order_id') }}, ',', '') as bigint) end as k
    from {{ source('raw', 'orders') }}
),

flagged as (
    select
        typed.*,
        list_filter([
            case when _key_seq > 1 then 'payments__pk__unique' end,
            case when payment_id__text is null then 'payments__payment_id__not_null' end,
            case when order_ref__text is null then 'payments__order_ref__not_null' end,
            case when (order_ref__text is not null and order_ref is null) then 'payments__order_ref__type' end,
            case when paid_at__text is null then 'payments__paid_at__not_null' end,
            case when (paid_at__text is not null and paid_at is null) then 'payments__paid_at__type' end,
            case when coalesce(paid_at < timestamp '1990-01-01' or paid_at >= timestamp '2026-02-01' + interval 1 day, false) then 'payments__paid_at__window' end,
            case when method__text is null then 'payments__method__not_null' end,
            case when (method__text is not null and lower(method__text) not in ('bank_transfer', 'card', 'cod', 'paypal_x', 'wallet')) then 'payments__method__in_set' end,
            case when amount__text is null then 'payments__amount__not_null' end,
            case when (amount__text is not null and amount is null) then 'payments__amount__type' end,
            case when coalesce(amount < 0, false) then 'payments__amount__non_negative' end,
            case when currency__text is null then 'payments__currency__not_null' end,
            case when (currency__text is not null and lower(currency__text) not in ('aed', 'sar', 'usd')) then 'payments__currency__in_set' end,
            case when is_settled__text is null then 'payments__is_settled__not_null' end,
            case when (is_settled__text is not null and is_settled is null) then 'payments__is_settled__type' end,
            case when (order_ref is not null and parent_0.k is null) then 'payments__order_ref__references_orders' end
        ], x -> x is not null) as failed_rules,
        list_filter([
            case when coalesce(amount > 118492.424, false) then 'payments__amount__outlier' end
        ], x -> x is not null) as warned_rules
    from typed
    left join parent_0 on parent_0.k = typed.order_ref
)

select * from flagged
