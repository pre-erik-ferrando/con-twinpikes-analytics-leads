{{ config(materialized='view') }}
-- Salesforce's monthly status file as received (DT.8.4), one row per file row, with its file's month and
-- import date. The engine maps it with biz_cfg_source_column (SF_STATUS) into sf_status_out.
SELECT
    r.file_id                    AS status_file_id,
    r.source_row_id              AS source_row_id,
    r.row_json                   AS row_json,
    f.status_month               AS file_status_month,
    f.file_name                  AS file_name,
    f.loaded_at                  AS imported_at
FROM {{ source('landing', 'upload_row') }} AS r
INNER JOIN {{ source('landing', 'upload_file') }} AS f ON f.file_id = r.file_id
WHERE f.kind = 'SF_STATUS'
SETTINGS join_algorithm = 'hash'
