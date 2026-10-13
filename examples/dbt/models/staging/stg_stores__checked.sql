{{ config(materialized='table') }}

with source as (
    select * from {{ source('raw', 'stores') }}
),

cleaned as (
    select
        _row_id,
        {{ clean_text('store_id') }} as store_id__text,
        {{ clean_text('store_name') }} as store_name__text,
        {{ clean_text('region') }} as region__text,
        {{ clean_text('opened_on') }} as opened_on__text,
        {{ clean_text('is_online') }} as is_online__text,
        {{ clean_text('square_meters') }} as square_meters__text
    from source
),

typed as (
    select
        *,
        store_id__text as store_id,
        store_name__text as store_name,
        region__text as region,
        cast(try_strptime(opened_on__text, ['%Y-%m-%d']) as date) as opened_on,
        case when lower(is_online__text) in ('1', 't', 'true', 'y', 'yes') then true when lower(is_online__text) in ('0', 'f', 'false', 'n', 'no') then false end as is_online,
        case when regexp_full_match(square_meters__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(square_meters__text, ',', '') as bigint) end as square_meters,
        case when store_id__text is not null then row_number() over (partition by store_id__text order by _row_id) else 1 end as _key_seq
    from cleaned
),

flagged as (
    select
        typed.*,
        list_filter([
            case when _key_seq > 1 then 'stores__pk__unique' end,
            case when store_id__text is null then 'stores__store_id__not_null' end,
            case when (store_id__text is not null and not regexp_full_match(store_id__text, '^[A-Z]{2}\-\d{4}$')) then 'stores__store_id__pattern' end,
            case when store_name__text is null then 'stores__store_name__not_null' end,
            case when region__text is null then 'stores__region__not_null' end,
            case when (region__text is not null and lower(region__text) not in ('central', 'east', 'north', 'online', 'south', 'west')) then 'stores__region__in_set' end,
            case when opened_on__text is null then 'stores__opened_on__not_null' end,
            case when (opened_on__text is not null and opened_on is null) then 'stores__opened_on__type' end,
            case when coalesce(opened_on < timestamp '1990-01-01' or opened_on >= timestamp '2026-02-01' + interval 1 day, false) then 'stores__opened_on__window' end,
            case when is_online__text is null then 'stores__is_online__not_null' end,
            case when (is_online__text is not null and is_online is null) then 'stores__is_online__type' end,
            case when (square_meters__text is not null and square_meters is null) then 'stores__square_meters__type' end,
            case when coalesce(square_meters < 0, false) then 'stores__square_meters__non_negative' end
        ], x -> x is not null) as failed_rules,
        list_filter([
            case when coalesce(square_meters > 58820.0, false) then 'stores__square_meters__outlier' end
        ], x -> x is not null) as warned_rules
    from typed
)

select * from flagged
