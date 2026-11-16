{{ config(materialized='view') }}
-- One row per business, run, profile and factor, with bucket, points and input value (DT.7.4).
SELECT
    c.business_id, c.country, c.profile, c.run_id, c.factor, c.bucket_code, c.points, c.input_value,
    c.review_reference_version, c.inserted_at
FROM {{ source('engine', 'score_component_out') }} AS c
