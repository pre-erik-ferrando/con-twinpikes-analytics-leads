{{ config(materialized='view') }}
-- The current Sales Ops decision per business and profile (biz_lead_validation, written by Intelek):
-- last row by (inserted_at, reviewed_by). The alias is resolved BEFORE taking the last row, so a merged
-- business keeps one decision (the latest of its own and its absorbed businesses'), never two rows.
SELECT
    v.business_id                                                 AS business_id,
    v.profile                                                     AS profile,
    argMax(v.country, (v.inserted_at, v.reviewed_by))             AS country,
    argMax(v.decision, (v.inserted_at, v.reviewed_by))            AS decision,
    argMax(v.exclusion_reason, (v.inserted_at, v.reviewed_by))    AS exclusion_reason,
    argMax(v.customer_code, (v.inserted_at, v.reviewed_by))       AS customer_code,
    argMax(v.duplicate_of, (v.inserted_at, v.reviewed_by))        AS duplicate_of,
    argMax(v.comment, (v.inserted_at, v.reviewed_by))             AS comment,
    argMax(v.approval_scope, (v.inserted_at, v.reviewed_by))      AS approval_scope,
    argMax(v.reviewed_by, (v.inserted_at, v.reviewed_by))         AS reviewed_by,
    max(v.inserted_at)                                            AS reviewed_at,
    argMax(v.capture_batch_id, (v.inserted_at, v.reviewed_by))    AS capture_batch_id
FROM (
    SELECT
        coalesce(al.surviving_id, src.pk_business_id)             AS business_id,
        src.pk_profile                                            AS profile,
        src.des_country                                           AS country,
        src.des_decision                                          AS decision,
        src.des_exclusion_reason                                  AS exclusion_reason,
        src.des_customer_code                                     AS customer_code,
        src.val_duplicate_of                                      AS duplicate_of,
        src.des_comment                                           AS comment,
        src.des_approval_scope                                    AS approval_scope,
        src.des_capture_batch_id                                  AS capture_batch_id,
        src.des_reviewed_by                                       AS reviewed_by,
        src.inserted_at                                           AS inserted_at
    FROM {{ source('writeback', 'biz_lead_validation') }} AS src
    LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = src.pk_business_id
) AS v
GROUP BY v.business_id, v.profile
SETTINGS join_use_nulls = 1, join_algorithm = 'hash'
