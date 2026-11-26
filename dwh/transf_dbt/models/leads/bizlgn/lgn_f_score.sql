{{ config(materialized='view') }}
-- The score of each lead (business x profile) as the rest of the views show it (DF.8, DT.7.4): the score
-- frozen when it was exported (export_lead_out, the latest export of the lead that is not FAILED) if it
-- has one, else the live score computed in SQL with the current configuration (lgn_f2_score): a lead
-- whose export failed can be exported again, with the live score. Total, Rating and breakdown in the
-- cockpit's order (type, brands, zone, digital, contact, reviews).
WITH exports AS (
    -- each export of the lead with its current status (confirm adds a row: the GENERATED one stays)
    SELECT
        f.business_id AS business_id, f.profile AS profile, f.export_id AS export_id,
        argMax(f.status, f.inserted_at) AS status, argMax(f.country, f.inserted_at) AS country,
        argMax(f.total, f.inserted_at) AS total, argMax(f.rating, f.inserted_at) AS rating,
        argMax(f.breakdown, f.inserted_at) AS breakdown, min(f.inserted_at) AS frozen_at
    FROM {{ source('engine', 'export_lead_out') }} AS f
    WHERE isNotNull(f.total)
    GROUP BY f.business_id, f.profile, f.export_id
),
frozen AS (
    SELECT
        e.business_id AS business_id, e.profile AS profile,
        argMax(e.country, e.frozen_at) AS country, argMax(assumeNotNull(e.total), e.frozen_at) AS total,
        argMax(assumeNotNull(e.rating), e.frozen_at) AS rating, argMax(e.breakdown, e.frozen_at) AS breakdown,
        argMax(e.export_id, e.frozen_at) AS export_id, max(e.frozen_at) AS frozen_at
    FROM exports AS e
    WHERE e.status <> 'FAILED'
    GROUP BY e.business_id, e.profile
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
