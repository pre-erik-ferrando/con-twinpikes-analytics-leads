{{ config(materialized='view') }}
-- A lead's state in Salesforce per month (DT.8.4), key (lead_code, status_month): the latest import of
-- each month wins. The current state is the one of the latest month (is_current = 1).
SELECT
    q.lead_code, q.business_id, q.country, q.status_month, q.status, q.discard_reason, q.subregion_code,
    q.sf_lead_id, q.company_name, q.status_file_id, q.inserted_at AS imported_at,
    toUInt8(q.status_month = max(q.status_month) OVER (PARTITION BY q.lead_code)) AS is_current
FROM (
    SELECT o.*,
           row_number() OVER (PARTITION BY o.lead_code, o.status_month ORDER BY o.inserted_at DESC) AS rn
    FROM {{ source('engine', 'sf_status_out') }} AS o
) AS q
WHERE q.rn = 1
