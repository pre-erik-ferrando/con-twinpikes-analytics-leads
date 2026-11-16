{{ config(materialized='view') }}
-- Indicators of the validation screen (DF.13): per capture batch and profile.
SELECT
    l.des_capture_batch_id                                                AS pk_capture_batch_id,
    l.pk_profile                                                          AS pk_profile,
    any(l.des_country)                                                    AS des_country,
    count()                                                               AS val_found,
    countIf(l.des_status = 'Excluded by engine')                          AS val_excluded_by_engine,
    countIf(l.des_status = 'To review')                                   AS val_to_review,
    countIf(l.des_status = 'Rejected')                                    AS val_rejected,
    countIf(l.des_status = 'Approved')                                    AS val_approved,
    countIf(l.des_status = 'Published')                                   AS val_published,
    round(countIf(l.des_status IN ('Approved', 'Published'))
          / nullIf(countIf(l.des_status IN ('Approved', 'Published', 'Rejected')), 0), 3) AS val_approval_rate,
    countIf(l.des_rating = 'HOT')                                         AS val_hot,
    countIf(l.des_rating = 'WARM')                                        AS val_warm,
    countIf(l.des_rating = 'COLD')                                        AS val_cold
FROM {{ ref('lgn_v_lead') }} AS l
GROUP BY l.des_capture_batch_id, l.pk_profile
