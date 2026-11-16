{#- A var as a quoted SQL literal: {{ sql_str(var('others_family_id')) }} -> 'OTHERS'. -#}
{% macro sql_str(value) %}'{{ value }}'{% endmacro %}
