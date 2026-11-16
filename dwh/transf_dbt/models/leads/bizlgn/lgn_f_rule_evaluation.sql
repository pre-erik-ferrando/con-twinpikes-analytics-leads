{{ config(materialized='view') }}
-- The current relevance-filter decision per business and profile (DT.4.4): the latest run's.
SELECT
    q.business_id, q.country, q.profile, q.decision, q.stage, q.rule, q.trigger_value, q.is_closed_temporarily,
    q.run_id, q.snapshot_id, q.inserted_at AS evaluated_at
FROM (
    SELECT r.*, row_number() OVER (PARTITION BY r.business_id, r.profile ORDER BY r.inserted_at DESC) AS rn
    FROM {{ source('engine', 'rule_evaluation_out') }} AS r
) AS q
WHERE q.rn = 1
