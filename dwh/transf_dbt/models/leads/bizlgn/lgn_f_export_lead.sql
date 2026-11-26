{{ config(materialized='view') }}
-- A lead in an export and its current state (GENERATED, UPLOADED, FAILED). Published = UPLOADED. Under the
-- surviving business (state.exported): a lead exported before a merge stays exported, never twice.
SELECT
    e.export_id                                 AS export_id,
    coalesce(al.surviving_id, e.business_id)    AS business_id,
    any(e.country)                              AS country,
    any(e.profile)                              AS profile,
    any(e.capture_batch_id)                     AS capture_batch_id,
    any(e.lead_code)                            AS lead_code,
    argMax(e.status, e.inserted_at)             AS status,
    min(e.inserted_at)                          AS generated_at,
    max(e.inserted_at)                          AS status_at
FROM {{ source('engine', 'export_lead_out') }} AS e
LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = e.business_id
GROUP BY e.export_id, coalesce(al.surviving_id, e.business_id)
SETTINGS join_use_nulls = 1, join_algorithm = 'hash'
