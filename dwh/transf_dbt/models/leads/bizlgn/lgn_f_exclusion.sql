{{ config(materialized='view') }}
-- Permanent exclusions by business, with origin and reason (DT.8.1), aliases resolved, read from their
-- sources. The only implementation: the engine reads it (state.permanent_exclusions, the filter);
-- tests_db checks it against tests/sql_reference.py.
-- - HUMAN: the current Sales Ops decision of the business (latest of any profile) is EXCLUDED;
-- - SALESFORCE: the lead was DISCARDED in Salesforce in some month (latest import of the month), for good
--   (v3.3 DT.8.4), with the reason of its latest such month.
SELECT
    h.business_id AS business_id, h.country AS country, 'ALL' AS scope, h.profile AS profile,
    'HUMAN' AS origin, h.reason AS reason, concat('validation:', toString(h.excluded_at)) AS source_ref,
    h.excluded_at AS excluded_at
FROM (
    SELECT
        v.business_id                                                              AS business_id,
        argMax(v.country, (v.reviewed_at, v.reviewed_by))                          AS country,
        argMax(v.profile, (v.reviewed_at, v.reviewed_by))                          AS profile,
        argMax(v.decision, (v.reviewed_at, v.reviewed_by))                         AS decision,
        ifNull(argMax(v.exclusion_reason, (v.reviewed_at, v.reviewed_by)), 'NOT_VALID') AS reason,
        max(v.reviewed_at)                                                         AS excluded_at
    FROM {{ ref('lgn_f_validation') }} AS v
    GROUP BY v.business_id
) AS h
WHERE h.decision = 'EXCLUDED'
UNION ALL
SELECT
    s.business_id AS business_id, s.country AS country, 'ALL' AS scope, CAST(NULL AS Nullable(String)) AS profile,
    'SALESFORCE' AS origin, s.reason AS reason, s.source_ref AS source_ref, s.excluded_at AS excluded_at
FROM (
    SELECT
        d.business_id                                                              AS business_id,
        argMax(d.country, d.status_month)                                          AS country,
        ifNull(argMax(d.discard_reason, d.status_month), 'DISCARDED')              AS reason,
        argMax(concat('sf:', d.status_file_id, ':', d.lead_code), d.status_month)  AS source_ref,
        argMax(d.imported_at, d.status_month)                                      AS excluded_at
    FROM {{ ref('lgn_d_sf_status') }} AS d
    WHERE d.status = 'DISCARDED'
    GROUP BY d.business_id
) AS s
