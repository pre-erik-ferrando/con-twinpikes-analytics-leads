{{ config(materialized='view') }}
-- A lead's state in Salesforce per month (DT.8.4), key (lead_code, status_month): the rows of the status
-- files without issue (lgn_d_sf_status_row), the latest import of each month wins. The current state is
-- the one of the latest month (is_current = 1).
SELECT
    q.lead_code, q.business_id, q.country, q.status_month, q.status, q.discard_reason, q.subregion_code,
    q.sf_lead_id, q.company_name, q.status_file_id, q.imported_at,
    toUInt8(q.status_month = max(q.status_month) OVER (PARTITION BY q.lead_code)) AS is_current
FROM (
    SELECT
        assumeNotNull(r.lead_code) AS lead_code, assumeNotNull(r.business_id) AS business_id,
        assumeNotNull(r.country) AS country, r.status_month AS status_month, r.status AS status,
        r.discard_reason AS discard_reason, r.subregion_code AS subregion_code, r.sf_lead_id AS sf_lead_id,
        r.company_name AS company_name, r.status_file_id AS status_file_id, r.imported_at AS imported_at,
        row_number() OVER (PARTITION BY r.lead_code, r.status_month ORDER BY r.imported_at DESC, r.row_no DESC) AS rn
    FROM {{ ref('lgn_d_sf_status_row') }} AS r
    WHERE isNull(r.issue)
) AS q
WHERE q.rn = 1
