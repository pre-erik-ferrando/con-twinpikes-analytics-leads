{#- `+schema` is the literal database name, no `<target>_` prefix. Without it the model lands in
    target.schema, which is the landing database. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name | trim }}
    {%- endif -%}
{%- endmacro %}
