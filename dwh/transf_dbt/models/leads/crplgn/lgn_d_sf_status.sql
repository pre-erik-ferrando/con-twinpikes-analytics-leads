{{ config(materialized='view') }}
-- A lead's state in Salesforce per month (DT.8.4), key (lead_code, status_month): the latest import of
-- each month wins. The current state is the one of the latest month (is_current = 1).
-- Salesforce's status files as received (upload_row), mapped with the CURRENT biz_cfg_source_column
-- (SF_STATUS, common rows) as loaders.sf_status_rows does in Python (keep them equal): transforms TRIM,
-- UPPER and VALUE_MAP (the ones the SF_STATUS mapping uses; any other passes the value as received).
-- Only lead codes the tool exported and statuses that map; aliases resolved one level.
WITH mapping AS (
    SELECT m.source_column AS source_column, m.target_column AS target_column, m.transform AS transform,
           m.value_map AS value_map
    FROM {{ ref('lgn_a_source_column') }} AS m
    WHERE m.source_system = 'SF_STATUS' AND isNull(m.country)
),
cells AS (
    -- normalize.none_if_sentinel: trimmed, and the sentinel values become NULL
    SELECT x.file_id AS file_id, x.row_no AS row_no, x.loaded_at AS loaded_at, x.target_column AS target_column,
           x.transform AS transform, x.value_map AS value_map, x.raw AS raw,
           if(isNull(x.raw) OR lowerUTF8(x.trimmed) IN ('', 'no website', 'no web', 'not checked', '#n/a', 'n/a',
                                                         'xxx', 'not_applicable', 'unknown', 'none', 'null', '-'),
              NULL, x.trimmed)                                                            AS clean
    FROM (
        SELECT r.file_id AS file_id, toUInt32OrZero(r.source_row_id) AS row_no, f.loaded_at AS loaded_at,
               m.target_column AS target_column, m.transform AS transform, m.value_map AS value_map,
               JSONExtract(r.row_json, m.source_column, 'Nullable(String)') AS raw,
               replaceRegexpAll(ifNull(raw, ''), '^\\s+|\\s+$', '') AS trimmed
        FROM {{ source('landing', 'upload_row') }} AS r
        INNER JOIN {{ source('landing', 'upload_file') }} AS f ON f.file_id = r.file_id
        CROSS JOIN mapping AS m
        WHERE f.kind = 'SF_STATUS'
    ) AS x
),
mapped AS (
    SELECT c.file_id AS file_id, c.row_no AS row_no, c.loaded_at AS loaded_at, c.target_column AS target_column,
           multiIf(c.transform = 'TRIM', c.clean,
                   c.transform = 'UPPER', upperUTF8(c.clean),
                   c.transform = 'VALUE_MAP', v.target_value,
                   c.raw)                                                                AS value
    FROM cells AS c
    LEFT JOIN (SELECT vm.map_name AS map_name, vm.source_value AS source_value, vm.target_value AS target_value
               FROM {{ ref('lgn_a_value_map') }} AS vm WHERE isNull(vm.country)) AS v
           ON v.map_name = c.value_map AND v.source_value = ifNull(c.clean, '')
),
file_rows AS (
    SELECT m.file_id AS file_id, m.row_no AS row_no, any(m.loaded_at) AS loaded_at,
           anyIf(m.value, m.target_column = 'lead_code')       AS lead_code,
           anyIf(m.value, m.target_column = 'sf_lead_id')      AS sf_lead_id,
           anyIf(m.value, m.target_column = 'company_name')    AS company_name,
           anyIf(m.value, m.target_column = 'status')          AS status,
           anyIf(m.value, m.target_column = 'status_month')    AS status_month,
           anyIf(m.value, m.target_column = 'subregion_code')  AS subregion_code,
           anyIf(m.value, m.target_column = 'discard_reason')  AS discard_reason
    FROM mapped AS m
    GROUP BY m.file_id, m.row_no
),
known AS (
    -- loaders.parse_lead_code: <country>-<business_id>; only codes the tool exported
    SELECT fr.*, extract(assumeNotNull(fr.lead_code), '^([A-Z]{2})-\\d+$') AS country,
           toUInt64OrNull(extract(assumeNotNull(fr.lead_code), '^[A-Z]{2}-(\\d+)$')) AS code_business_id
    FROM file_rows AS fr
    WHERE fr.lead_code IN (SELECT e.lead_code FROM {{ source('engine', 'export_lead_out') }} AS e)
      AND isNotNull(fr.status)
)
SELECT
    q.lead_code, q.business_id, q.country, q.status_month, q.status, q.discard_reason, q.subregion_code,
    q.sf_lead_id, q.company_name, q.status_file_id, q.imported_at,
    toUInt8(q.status_month = max(q.status_month) OVER (PARTITION BY q.lead_code)) AS is_current
FROM (
    SELECT
        assumeNotNull(k.lead_code) AS lead_code,
        coalesce(al.surviving_id, assumeNotNull(k.code_business_id)) AS business_id,
        k.country AS country, ifNull(k.status_month, '') AS status_month, k.status AS status,
        k.discard_reason AS discard_reason, k.subregion_code AS subregion_code, k.sf_lead_id AS sf_lead_id,
        k.company_name AS company_name, k.file_id AS status_file_id, k.loaded_at AS imported_at,
        row_number() OVER (PARTITION BY k.lead_code, ifNull(k.status_month, '')
                           ORDER BY k.loaded_at DESC, k.row_no DESC) AS rn
    FROM known AS k
    LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = k.code_business_id
    WHERE isNotNull(k.code_business_id)
) AS q
WHERE q.rn = 1
SETTINGS join_use_nulls = 1
