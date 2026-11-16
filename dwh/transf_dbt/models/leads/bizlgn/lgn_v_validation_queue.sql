{{ config(materialized='view') }}
-- The leads Sales Ops validates (DF.9, DT.8.1): scored and not excluded, decision PENDING or none.
-- Ordered for the call: score desc, then reviews, then name (DT.7.4).
SELECT
    l.pk_business_id, l.pk_profile, l.des_country, l.des_lead_code, l.des_capture_batch_id, l.des_name,
    l.des_subregion, l.des_quadrant, l.des_company_type, l.val_score, l.des_rating,
    l.val_points_company_type, l.val_points_brands, l.val_points_zone, l.val_points_digital,
    l.val_points_contact, l.val_points_reviews, l.des_why, l.des_competitor_brands, l.des_fluidra_brands,
    l.des_contact_name, l.des_email, l.des_phone, l.des_website, l.des_crawl_status, l.des_web_validation,
    l.des_review_reason, l.des_customer_candidates, l.val_is_merge_suspect, l.val_is_indirect_customer,
    row_number() OVER (PARTITION BY l.des_country, l.pk_profile
                       ORDER BY l.val_score DESC, l.val_reviews DESC, l.des_name ASC)     AS val_position
FROM {{ ref('lgn_v_lead') }} AS l
WHERE l.des_status = 'To review'
