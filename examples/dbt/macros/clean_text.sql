{% macro clean_text(column) -%}
    case
        when {{ column }} is null or lower(trim({{ column }})) in ('', '-', 'n/a', 'na', 'nan', 'nil', 'none', 'null') then null
        else trim({{ column }})
    end
{%- endmacro %}
