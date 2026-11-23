{{ config(materialized='view') }}
-- Every row of Salesforce's status files (DT.8.4) as the engine reads it: mapped with the CURRENT
-- biz_cfg_source_column (SF_STATUS, common rows), transforms TRIM, UPPER and VALUE_MAP (the ones the
-- SF_STATUS mapping uses; any other passes the value as received). The only implementation of the mapping:
-- load-sf-status reports its warnings from here and lgn_d_sf_status keeps the rows without issue.
--   issue: UNKNOWN_LEAD_CODE (not <country>-<business_id> of a lead the tool exported) or UNMAPPED_STATUS;
--   zone_mismatch: the file's subregion is not one of the lead's listings' (the lead keeps its own).
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
parsed AS (
    -- loaders.parse_lead_code: <country>-<business_id>
    SELECT fr.*, extract(ifNull(fr.lead_code, ''), '^([A-Z]{2})-\\d+$') AS code_country,
           toUInt64OrNull(extract(ifNull(fr.lead_code, ''), '^[A-Z]{2}-(\\d+)$')) AS code_business_id,
           ifNull(fr.lead_code, '') IN (SELECT e.lead_code FROM {{ source('engine', 'export_lead_out') }} AS e) AS exported
    FROM file_rows AS fr
),
own_zones AS (
    SELECT bp.business_id AS business_id, groupUniqArray(p.subregion_code) AS zones
    FROM {{ ref('lgn_d_business_place') }} AS bp
    INNER JOIN {{ ref('lgn_d_place') }} AS p ON p.place_id = bp.place_id
    GROUP BY bp.business_id
)
SELECT
    p.file_id AS status_file_id, p.row_no AS row_no, p.loaded_at AS imported_at, p.lead_code AS lead_code,
    if(p.exported AND isNotNull(p.code_business_id), coalesce(al.surviving_id, p.code_business_id), NULL) AS business_id,
    nullIf(p.code_country, '') AS country, p.sf_lead_id AS sf_lead_id, p.company_name AS company_name,
    p.status AS status, ifNull(p.status_month, '') AS status_month, p.subregion_code AS subregion_code,
    p.discard_reason AS discard_reason,
    CAST(multiIf(NOT p.exported OR isNull(p.code_business_id), 'UNKNOWN_LEAD_CODE',
                 isNull(p.status), 'UNMAPPED_STATUS', NULL) AS Nullable(String)) AS issue,
    toUInt8(p.exported AND isNotNull(p.code_business_id) AND isNotNull(p.status)
            AND notEmpty(ifNull(p.subregion_code, '')) AND notEmpty(ifNull(z.zones, []))
            AND NOT has(ifNull(z.zones, []), p.subregion_code)) AS zone_mismatch
FROM parsed AS p
LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = p.code_business_id
LEFT JOIN own_zones AS z ON z.business_id = coalesce(al.surviving_id, p.code_business_id)
SETTINGS join_use_nulls = 1, join_algorithm = 'hash'
