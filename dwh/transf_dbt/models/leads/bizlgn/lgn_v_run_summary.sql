{{ config(materialized='view') }}
-- One row per run: type, current state, who and when, its message and cost (DT.9).
WITH costs AS (
    SELECT c.run_id AS run_id,
           sumIf(c.amount_usd, c.kind = 'ESTIMATED') AS estimated_usd,
           sumIf(c.amount_usd, c.kind = 'ACTUAL')    AS actual_usd
    FROM {{ source('ops', 'cost_ledger') }} AS c
    GROUP BY c.run_id
)
SELECT
    r.run_id                                            AS pk_run_id,
    any(r.run_type)                                     AS des_run_type,
    argMax(r.state, r.inserted_at)                      AS des_state,
    any(r.country)                                      AS des_country,
    any(r.profile)                                      AS des_profile,
    argMax(r.capture_batch_id, r.inserted_at)           AS des_capture_batch_id,
    argMin(r.requested_by, r.inserted_at)               AS des_requested_by,
    min(r.inserted_at)                                  AS dt_requested_at,
    max(r.inserted_at)                                  AS dt_last_change,
    argMax(r.message, r.inserted_at)                    AS des_message,
    any(k.estimated_usd)                                AS imp_estimated_usd,
    any(k.actual_usd)                                   AS imp_actual_usd
FROM {{ source('ops', 'pipeline_run') }} AS r
LEFT JOIN costs AS k ON k.run_id = r.run_id
GROUP BY r.run_id
SETTINGS join_use_nulls = 1, join_algorithm = 'hash'
