{{ config(materialized='view') }}
-- The latest call result per lead_code, by export date of the cockpit file (DT.8.4).
SELECT
    q.lead_code, q.business_id, q.country, q.status, q.not_qualified_reason, q.customer_code, q.attempts,
    q.potential_entered_eur, q.potential_indicative_eur, q.answers_json, q.contact_corrections_json,
    q.last_call_date, q.last_comment, q.exported_at, q.cockpit_file_id
FROM (
    SELECT o.*,
           row_number() OVER (PARTITION BY o.lead_code ORDER BY o.exported_at DESC, o.inserted_at DESC) AS rn
    FROM {{ source('engine', 'cockpit_outcome_out') }} AS o
) AS q
WHERE q.rn = 1
