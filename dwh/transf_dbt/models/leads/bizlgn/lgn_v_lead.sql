{{ config(materialized='view') }}
-- What Intelek reads (DT.8.2): one row per business and profile found by a capture, with its latest
-- state and every filter as a literal column, because Intelek does not do joins. Style A names.
--
-- des_status, DF.4's six states, computed (never stored: insert-only model), review "Estado del lead":
--   Published           its export was confirmed UPLOADED
--   Rejected            Sales Ops' current decision is EXCLUDED
--   Excluded by engine  filter EXCLUDE, customer match, NOT_VALID website, or a Salesforce/engine exclusion
-- A published lead stays Published whatever its state in Salesforce (des_sf_status, DT.8.4).
--   Approved            current decision ACCEPTED, not published
--   To review           scored, decision PENDING or none
--   Found               in a capture batch, nothing else yet
-- Aliases are resolved in `leads` (like lgn_d_business, lgn_f_validation and lgn_f_exclusion): a merged
-- business is one row under its surviving id, with the captures of the businesses it absorbed.
WITH leads AS (
    SELECT
        coalesce(al.surviving_id, cbl.business_id)        AS business_id,
        cbl.profile                                       AS profile,
        any(cbl.country)                                  AS country,
        min(cbl.inserted_at)                              AS first_found_at,
        argMax(cbl.capture_batch_id, cbl.inserted_at)     AS capture_batch_id,
        count()                                           AS n_captures
    FROM {{ source('ops', 'capture_batch_lead') }} AS cbl
    LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = cbl.business_id
    GROUP BY coalesce(al.surviving_id, cbl.business_id), cbl.profile
),
published AS (
    SELECT e.business_id AS business_id, min(e.status_at) AS published_at
    FROM {{ ref('lgn_f_export_lead') }} AS e
    WHERE e.status = 'UPLOADED'
    GROUP BY e.business_id
),
engine_exclusion AS (
    SELECT x.business_id AS business_id, argMax(x.origin, x.excluded_at) AS origin,
           argMax(x.reason, x.excluded_at) AS reason
    FROM {{ ref('lgn_f_exclusion') }} AS x
    WHERE x.origin <> 'HUMAN'
    GROUP BY x.business_id
)
SELECT
    l.business_id                                                     AS pk_business_id,
    l.profile                                                         AS pk_profile,
    l.country                                                         AS des_country,
    concat(l.country, '-', toString(l.business_id))                   AS des_lead_code,
    multiIf(isNotNull(pub.business_id), 'Published',
            v.decision = 'EXCLUDED', 'Rejected',
            r.decision = 'EXCLUDE' OR m.outcome = 'CUSTOMER' OR e.validation = 'NOT_VALID'
                OR isNotNull(xe.business_id), 'Excluded by engine',
            v.decision = 'ACCEPTED', 'Approved',
            isNotNull(s.business_id), 'To review',
            'Found')                                                  AS des_status,
    -- identification and address
    b.name                                                            AS des_name,
    e.legal_name                                                      AS des_legal_name,
    b.address                                                         AS des_address,
    b.city                                                            AS des_city,
    b.postal_code                                                     AS des_postal_code,
    b.subregion_code                                                  AS des_subregion_code,
    sa.area_name                                                      AS des_subregion,
    sa.region_name                                                    AS des_region,
    b.municipality_code                                               AS des_municipality_code,
    z.quadrant                                                        AS des_quadrant,
    b.latitude                                                        AS val_latitude,
    b.longitude                                                       AS val_longitude,
    b.website                                                         AS des_website,
    -- contact (phone from the website, else Google's)
    e.contact_name                                                    AS des_contact_name,
    e.contact_role                                                    AS des_contact_role,
    e.email                                                           AS des_email,
    coalesce(e.phone_e164, b.phone_e164)                              AS des_phone,
    if(isNotNull(e.phone_e164), 'WEB', if(isNotNull(b.phone_e164), 'GOOGLE', NULL)) AS des_phone_source,
    if(e.vat_check_status = 'VALID', e.vat_id, NULL)                  AS des_vat_id,
    -- what Google and the website say
    arrayStringConcat(b.categories, ', ')                             AS des_categories,
    b.owner_description                                               AS des_owner_description,
    e.validation                                                      AS des_web_validation,
    e.review_reason                                                   AS des_review_reason,
    e.validation_reason_text                                          AS des_why,
    e.company_type                                                    AS des_company_type,
    e.offering                                                        AS des_offering,
    e.pool_focus                                                      AS des_pool_focus,
    e.has_ecommerce                                                   AS val_has_ecommerce,
    arrayStringConcat(e.detected_brands, ', ')                        AS des_competitor_brands,
    arrayStringConcat(e.fluidra_brands, ', ')                         AS des_fluidra_brands,
    e.is_indirect_customer                                            AS val_is_indirect_customer,
    e.crawl_status                                                    AS des_crawl_status,
    e.n_unsupported_claims                                            AS val_unsupported_claims,
    b.rating                                                          AS val_google_rating,
    b.reviews_count                                                   AS val_reviews,
    -- score
    s.total                                                           AS val_score,
    s.rating                                                          AS des_rating,
    s.breakdown[1]                                                    AS val_points_company_type,
    s.breakdown[2]                                                    AS val_points_brands,
    s.breakdown[3]                                                    AS val_points_zone,
    s.breakdown[4]                                                    AS val_points_digital,
    s.breakdown[5]                                                    AS val_points_contact,
    s.breakdown[6]                                                    AS val_points_reviews,
    -- every gate, literal
    r.decision                                                        AS des_filter_decision,
    r.stage                                                           AS des_filter_stage,
    r.rule                                                            AS des_filter_rule,
    r.trigger_value                                                   AS des_filter_value,
    r.is_closed_temporarily                                           AS val_is_closed_temporarily,
    m.outcome                                                         AS des_customer_match,
    m.reason_text                                                     AS des_customer_match_reason,
    m.candidates_json                                                 AS des_customer_candidates,
    xe.origin                                                         AS des_exclusion_origin,
    xe.reason                                                         AS des_exclusion_reason,
    b.is_merge_suspect                                                AS val_is_merge_suspect,
    -- human validation, export and call
    v.decision                                                        AS des_decision,
    v.exclusion_reason                                                AS des_rejection_reason,
    v.reviewed_by                                                     AS des_reviewed_by,
    v.reviewed_at                                                     AS dt_reviewed_at,
    pub.published_at                                                  AS dt_published_at,
    sf.status                                                         AS des_sf_status,
    sf.discard_reason                                                 AS des_sf_discard_reason,
    sf.status_month                                                   AS des_sf_status_month,
    -- appearances
    l.capture_batch_id                                                AS des_capture_batch_id,
    l.n_captures                                                      AS val_captures,
    l.first_found_at                                                  AS dt_first_found_at,
    b.last_seen_at                                                    AS dt_last_seen_at,
    s.run_id                                                          AS des_last_scoring_run
FROM leads AS l
LEFT JOIN {{ ref('lgn_d_business') }} AS b ON b.business_id = l.business_id
LEFT JOIN {{ ref('lgn_d_enrichment') }} AS e ON e.business_id = l.business_id AND e.profile = l.profile
LEFT JOIN {{ ref('lgn_f_score') }} AS s ON s.business_id = l.business_id AND s.profile = l.profile
LEFT JOIN {{ ref('lgn_f_rule_evaluation') }} AS r ON r.business_id = l.business_id AND r.profile = l.profile
LEFT JOIN {{ ref('lgn_f_customer_match') }} AS m ON m.business_id = l.business_id
LEFT JOIN {{ ref('lgn_f_validation') }} AS v ON v.business_id = l.business_id AND v.profile = l.profile
LEFT JOIN published AS pub ON pub.business_id = l.business_id
LEFT JOIN engine_exclusion AS xe ON xe.business_id = l.business_id
LEFT JOIN {{ ref('lgn_d_sf_status') }} AS sf ON sf.business_id = l.business_id AND sf.is_current = 1
LEFT JOIN {{ ref('lgn_a_search_area') }} AS sa
       ON sa.country = l.country AND sa.admin_code = b.subregion_code AND sa.level = 'SUBREGION'
LEFT JOIN {{ ref('lgn_a_strategic_zone') }} AS z ON z.country = l.country AND z.admin_code = b.subregion_code
SETTINGS join_use_nulls = 1
