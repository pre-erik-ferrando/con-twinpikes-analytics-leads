{{ config(materialized='view') }}
-- One row per category of a listing, as Google returns it (DT.1.5.2).
SELECT
    p.place_id                     AS place_id,
    p.run_id                       AS run_id,
    arrayJoin(p.categories)        AS category
FROM {{ ref('lgn_e_place') }} AS p
