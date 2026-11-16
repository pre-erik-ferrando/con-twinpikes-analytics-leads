{{ config(materialized='view') }}
-- A lead in an export and its current state (GENERATED, UPLOADED, FAILED). Published = UPLOADED.
SELECT
    e.export_id, e.business_id,
    any(e.country)                              AS country,
    any(e.profile)                              AS profile,
    any(e.capture_batch_id)                     AS capture_batch_id,
    any(e.lead_code)                            AS lead_code,
    argMax(e.status, e.inserted_at)             AS status,
    min(e.inserted_at)                          AS generated_at,
    max(e.inserted_at)                          AS status_at
FROM {{ source('engine', 'export_lead_out') }} AS e
GROUP BY e.export_id, e.business_id
