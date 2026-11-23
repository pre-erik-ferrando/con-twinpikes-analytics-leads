{{ config(materialized='view') }}
-- The score of each lead (business x profile) as the rest of the views show it (DF.8, DT.7.4): the score
-- frozen when it was exported (export_score_out, the latest export of the lead) if it has been exported,
-- else the live score computed in SQL with the current configuration (lgn_f2_score). Total, Rating and
-- breakdown in the cockpit's order (type, brands, zone, digital, contact, reviews).
WITH frozen AS (
    SELECT
        f.business_id AS business_id, f.profile AS profile,
        argMax(f.country, f.inserted_at) AS country, argMax(f.total, f.inserted_at) AS total,
        argMax(f.rating, f.inserted_at) AS rating, argMax(f.breakdown, f.inserted_at) AS breakdown,
        argMax(f.export_id, f.inserted_at) AS export_id, max(f.inserted_at) AS frozen_at
    FROM {{ source('engine', 'export_score_out') }} AS f
    GROUP BY f.business_id, f.profile
)
SELECT
    l.business_id AS business_id, l.country AS country, l.profile AS profile,
    l.total AS total, l.rating AS rating, l.breakdown AS breakdown,
    'LIVE' AS score_source, CAST(NULL AS Nullable(String)) AS export_id
FROM {{ ref('lgn_f2_score') }} AS l
WHERE (l.business_id, l.profile) NOT IN (SELECT business_id, profile FROM frozen)
UNION ALL
SELECT
    fr.business_id, fr.country, fr.profile, fr.total, fr.rating, fr.breakdown,
    'EXPORT', CAST(fr.export_id AS Nullable(String))
FROM frozen AS fr
