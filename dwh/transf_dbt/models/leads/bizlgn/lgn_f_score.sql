{{ config(materialized='view') }}
-- The current score per business and profile: total, Rating, breakdown in the cockpit's order (type,
-- brands, zone, digital, contact, reviews) and the review reference used (DT.7.4).
SELECT
    q.business_id, q.country, q.profile, q.total, q.rating, q.breakdown, q.review_reference_version,
    q.run_id, q.snapshot_id, q.inserted_at AS scored_at
FROM (
    SELECT s.*, row_number() OVER (PARTITION BY s.business_id, s.profile ORDER BY s.inserted_at DESC) AS rn
    FROM {{ source('engine', 'score_out') }} AS s
) AS q
WHERE q.rn = 1
