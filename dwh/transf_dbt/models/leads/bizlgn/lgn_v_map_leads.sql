{{ config(materialized='view') }}
-- Map 2: every lead in its place, coloured by state (DF.13).
SELECT
    l.pk_business_id, l.pk_profile, l.des_country, l.des_capture_batch_id, l.des_name, l.des_status,
    l.des_rating, l.val_score, l.val_latitude, l.val_longitude, l.des_subregion, l.des_quadrant, l.des_cockpit_status
FROM {{ ref('lgn_v_lead') }} AS l
WHERE isNotNull(l.val_latitude)
