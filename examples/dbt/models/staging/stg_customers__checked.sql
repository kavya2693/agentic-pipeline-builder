{{ config(materialized='table') }}

with source as (
    select * from {{ source('raw', 'customers') }}
),

cleaned as (
    select
        _row_id,
        {{ clean_text('customer_id') }} as customer_id__text,
        {{ clean_text('email') }} as email__text,
        {{ clean_text('first_name') }} as first_name__text,
        {{ clean_text('last_name') }} as last_name__text,
        {{ clean_text('signup_date') }} as signup_date__text,
        {{ clean_text('country') }} as country__text,
        {{ clean_text('loyalty_tier') }} as loyalty_tier__text,
        {{ clean_text('phone') }} as phone__text,
        {{ clean_text('postal_code') }} as postal_code__text,
        {{ clean_text('marketing_opt_in') }} as marketing_opt_in__text,
        {{ clean_text('birth_year') }} as birth_year__text
    from source
),

typed as (
    select
        *,
        case when regexp_full_match(customer_id__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(customer_id__text, ',', '') as bigint) end as customer_id,
        email__text as email,
        first_name__text as first_name,
        last_name__text as last_name,
        cast(try_strptime(signup_date__text, ['%m/%d/%Y', '%Y-%m-%d']) as date) as signup_date,
        country__text as country,
        loyalty_tier__text as loyalty_tier,
        phone__text as phone,
        postal_code__text as postal_code,
        case when lower(marketing_opt_in__text) in ('1', 't', 'true', 'y', 'yes') then true when lower(marketing_opt_in__text) in ('0', 'f', 'false', 'n', 'no') then false end as marketing_opt_in,
        case when regexp_full_match(birth_year__text, '^[+-]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)$') then try_cast(replace(birth_year__text, ',', '') as bigint) end as birth_year,
        case when customer_id__text is not null then row_number() over (partition by customer_id__text order by _row_id) else 1 end as _key_seq
    from cleaned
),

flagged as (
    select
        typed.*,
        list_filter([
            case when _key_seq > 1 then 'customers__pk__unique' end,
            case when customer_id__text is null then 'customers__customer_id__not_null' end,
            case when (customer_id__text is not null and customer_id is null) then 'customers__customer_id__type' end,
            case when email__text is null then 'customers__email__not_null' end,
            case when (email__text is not null and not regexp_full_match(email__text, '^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$')) then 'customers__email__pattern' end,
            case when first_name__text is null then 'customers__first_name__not_null' end,
            case when last_name__text is null then 'customers__last_name__not_null' end,
            case when signup_date__text is null then 'customers__signup_date__not_null' end,
            case when (signup_date__text is not null and signup_date is null) then 'customers__signup_date__type' end,
            case when coalesce(signup_date < timestamp '1990-01-01' or signup_date >= timestamp '2026-02-01' + interval 1 day, false) then 'customers__signup_date__window' end,
            case when country__text is null then 'customers__country__not_null' end,
            case when (country__text is not null and lower(country__text) not in ('ae', 'bh', 'kw', 'om', 'qa', 'sa')) then 'customers__country__in_set' end,
            case when loyalty_tier__text is null then 'customers__loyalty_tier__not_null' end,
            case when (loyalty_tier__text is not null and lower(loyalty_tier__text) not in ('bronze', 'gold', 'platinum', 'silver')) then 'customers__loyalty_tier__in_set' end,
            case when (phone__text is not null and not regexp_full_match(phone__text, '^\+\d{3}\ \d{2}\ \d{3}\ \d{4}$')) then 'customers__phone__pattern' end,
            case when postal_code__text is null then 'customers__postal_code__not_null' end,
            case when (postal_code__text is not null and not regexp_full_match(postal_code__text, '^\d{5}$')) then 'customers__postal_code__pattern' end,
            case when marketing_opt_in__text is null then 'customers__marketing_opt_in__not_null' end,
            case when (marketing_opt_in__text is not null and marketing_opt_in is null) then 'customers__marketing_opt_in__type' end,
            case when (birth_year__text is not null and birth_year is null) then 'customers__birth_year__type' end,
            case when coalesce(birth_year < 0, false) then 'customers__birth_year__non_negative' end
        ], x -> x is not null) as failed_rules,
        list_filter([
            case when coalesce(birth_year > 20060.0, false) then 'customers__birth_year__outlier' end
        ], x -> x is not null) as warned_rules
    from typed
)

select * from flagged
