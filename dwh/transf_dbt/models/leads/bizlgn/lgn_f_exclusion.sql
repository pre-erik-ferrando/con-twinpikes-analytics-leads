{{ config(materialized='view') }}
-- Permanent exclusions by business, with origin and reason (DT.8.1), aliases resolved.
SELECT
    coalesce(al.surviving_id, x.business_id) AS business_id,
    x.country, x.scope, x.profile, x.origin, x.reason, x.source_ref, x.inserted_at AS excluded_at
FROM {{ source('engine', 'exclusion_out') }} AS x
LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = x.business_id
SETTINGS join_use_nulls = 1
