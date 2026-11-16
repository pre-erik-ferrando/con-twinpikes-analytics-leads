{{ config(materialized='view') }}
-- Everything set aside, with stage, rule, value and a readable reason (DT.4.4.4, DT.5.7). Nothing is
-- deleted: excluded leads are visible with their reason, not recoverable by hand (DF.2).
SELECT
    l.pk_business_id, l.pk_profile, l.des_country, l.des_lead_code, l.des_name, l.des_capture_batch_id,
    l.des_subregion, l.des_categories, l.des_website,
    multiIf(l.des_status = 'Rejected', 'HUMAN_EXCLUSION',
            l.des_filter_decision = 'EXCLUDE', l.des_filter_stage,
            l.des_customer_match = 'CUSTOMER', 'CUSTOMER_MATCH',
            l.des_web_validation = 'NOT_VALID', 'NOT_VALID',
            l.des_exclusion_origin = 'COCKPIT', 'COCKPIT_EXCLUSION', 'OTHER')            AS des_stage,
    multiIf(l.des_status = 'Rejected', l.des_rejection_reason,
            l.des_filter_decision = 'EXCLUDE', l.des_filter_rule,
            l.des_customer_match = 'CUSTOMER', 'ALREADY_CUSTOMER',
            l.des_web_validation = 'NOT_VALID', 'NOT_VALID',
            l.des_exclusion_reason)                                                      AS des_rule,
    multiIf(l.des_filter_decision = 'EXCLUDE', l.des_filter_value,
            l.des_customer_match = 'CUSTOMER', l.des_customer_match_reason,
            l.des_web_validation = 'NOT_VALID', l.des_why, NULL)                         AS des_reason,
    l.dt_first_found_at
FROM {{ ref('lgn_v_lead') }} AS l
WHERE l.des_status IN ('Excluded by engine', 'Rejected')
