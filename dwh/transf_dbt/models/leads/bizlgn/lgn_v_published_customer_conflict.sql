{{ config(materialized='view') }}
-- Published leads that a later customer match marks as customers (review DT.5). They are not touched
-- (no write-back to Salesforce): this list is for Fluidra [PENDIENTE TWP: what they do with it].
SELECT
    l.pk_business_id, l.pk_profile, l.des_country, l.des_lead_code, l.des_name, l.dt_published_at,
    m.signal                     AS des_signal,
    m.customer_code              AS des_customer_code,
    m.customer_name              AS des_customer_name,
    m.similarity                 AS val_similarity,
    m.reason_text                AS des_reason,
    m.matched_at                 AS dt_matched_at
FROM {{ ref('lgn_v_lead') }} AS l
INNER JOIN {{ ref('lgn_f_customer_match') }} AS m ON m.business_id = l.pk_business_id
WHERE l.des_status = 'Published' AND m.outcome = 'CUSTOMER' AND m.matched_at > l.dt_published_at
