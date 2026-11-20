{{ config(materialized='view') }}
-- Salesforce's monthly status file as received (DT.8.4), one row per file row, with its file's month and
-- import date. The engine maps it with biz_cfg_source_column (SF_STATUS) into sf_status_out.
SELECT
    r.status_file_id             AS status_file_id,
    r.source_row_id              AS source_row_id,
    r.lead_code                  AS lead_code,
    r.row_json                   AS row_json,
    f.status_month               AS file_status_month,
    f.file_name                  AS file_name,
    f.loaded_at                  AS imported_at
FROM {{ source('landing', 'sf_status_row') }} AS r
LEFT JOIN {{ source('landing', 'sf_status_file') }} AS f ON f.status_file_id = r.status_file_id
SETTINGS join_use_nulls = 1
