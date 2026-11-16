{{ config(materialized='view') }}
-- Review cut-offs of each country (DT.7.3): every version, the latest flagged.
SELECT
    r.country, r.reference_version, r.p75, r.median, r.n, r.is_provisional, r.inserted_at,
    toUInt8(r.inserted_at = max(r.inserted_at) OVER (PARTITION BY r.country)) AS is_current
FROM {{ source('engine', 'review_reference_out') }} AS r
