{{ config(materialized='view') }}
-- Google categories seen and not classified yet, with their frequency (DT.4.4.4). They are classified
-- in Configuration as INCLUDE, CONDITIONAL or EXCLUDE and count from the next run.
SELECT
    c.country                                AS des_country,
    c.language                               AS des_language,
    c.category_normalized                    AS pk_category_normalized,
    any(c.category)                          AS des_category,
    sum(c.n_places)                          AS val_listings,
    min(c.inserted_at)                       AS dt_first_seen,
    max(c.inserted_at)                       AS dt_last_seen
FROM {{ source('engine', 'category_seen_out') }} AS c
WHERE c.category_normalized NOT IN (SELECT r.category_normalized FROM {{ ref('lgn_a_category_rule') }} AS r)
GROUP BY c.country, c.language, c.category_normalized
