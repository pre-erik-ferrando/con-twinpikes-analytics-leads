{{ config(materialized='view') }}
-- Acceptance (DF.12): each company of the control set, found or not, and where it ended. A MUST_INCLUDE
-- company that was found must reach the list; a MUST_EXCLUDE one (a competitor's branch) must not.
WITH found AS (
    SELECT b.business_id AS business_id, b.country AS country, b.domain AS domain, arrayJoin(
        if(empty(b.phones), [''], b.phones)) AS phone
    FROM {{ ref('lgn_d_business') }} AS b
)
SELECT
    c.country                                                AS pk_country,
    c.source                                                 AS pk_source,
    c.name                                                   AS pk_name,
    c.expected                                               AS des_expected,
    c.expected_reason                                        AS des_expected_reason,
    any(f.business_id)                                       AS val_business_id,
    any(l.des_status)                                        AS des_status,
    toUInt8(isNotNull(any(f.business_id)))                   AS val_is_found,
    multiIf(isNull(any(f.business_id)), NULL,
            c.expected = 'MUST_INCLUDE', toUInt8(any(l.des_status) NOT IN ('Excluded by engine', 'Found')),
            toUInt8(any(l.des_status) = 'Excluded by engine'))                        AS val_is_ok
FROM {{ ref('lgn_a_control_set') }} AS c
LEFT JOIN found AS f
       ON f.country = c.country AND ((isNotNull(c.domain) AND f.domain = c.domain)
                                     OR (isNotNull(c.phone) AND f.phone = c.phone))
LEFT JOIN {{ ref('lgn_v_lead') }} AS l ON l.pk_business_id = f.business_id
GROUP BY c.country, c.source, c.name, c.expected, c.expected_reason
SETTINGS join_use_nulls = 1
