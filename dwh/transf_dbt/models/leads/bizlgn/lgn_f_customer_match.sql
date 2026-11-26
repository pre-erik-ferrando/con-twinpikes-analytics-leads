{{ config(materialized='view') }}
-- The customer-match row that decides for each business (DT.5.4): human or Salesforce correction > hard
-- signal > model verdict > soft confirmation, among the latest automatic pass, the latest Sales Ops
-- correction (none if it is RETRACTED: the lead was reopened) and every Salesforce correction.
-- Same rule as matching.resolve_priority + steps.common.deciding_matches.
WITH ranked AS (
    SELECT
        m.*,
        multiIf(m.origin IN ('HUMAN', 'SALESFORCE'), 0,
                m.signal IN ('VAT', 'REGISTER', 'PHONE', 'DOMAIN', 'FLUIDRA_ENTITY'), 1,
                m.signal = 'MODEL', 2,
                m.signal = 'NAME_CONFIRM', 3, 9)                                      AS priority_level,
        toUInt8(m.outcome = 'CUSTOMER' OR m.origin <> 'AUTO')                         AS is_deciding,
        row_number() OVER (PARTITION BY m.business_id, m.origin ORDER BY m.inserted_at DESC)       AS rn_origin
    FROM {{ source('engine', 'customer_match_out') }} AS m
),
picked AS (
    SELECT r.*,
           row_number() OVER (PARTITION BY r.business_id
                              ORDER BY r.is_deciding DESC, r.priority_level ASC, r.inserted_at DESC) AS rn
    FROM ranked AS r
    WHERE r.origin = 'SALESFORCE' OR (r.rn_origin = 1 AND r.outcome <> 'RETRACTED')
)
SELECT
    p.business_id, p.country, p.match_pass, p.origin, p.outcome, p.signal, p.subsidiary_code, p.customer_code,
    p.customer_name, p.similarity, p.verdict, p.candidates_json, p.n_candidates_dropped, p.reason_text,
    p.extract_id, p.run_id, p.inserted_at AS matched_at
FROM picked AS p
WHERE p.rn = 1
