{{ config(materialized='view') }}
-- The profile score computed in SQL (DF.8, DT.7): one row per lead (business x profile), factor and bucket,
-- with its points. The only implementation of the score (it replaced the engine's scoring.py, 2026-11-23;
-- tests_db checks it against that engine's results, golden_scores.json, and against the definition's
-- examples). It uses the CURRENT configuration and review reference: an exported lead keeps the score
-- frozen at its export (export_lead_out, shown by lgn_f_score).
WITH leads AS (
    -- VALID and REVIEW leads (NOT_VALID is never scored) that are not customers (enrich and reprocess
    -- leave customers out before scoring), with their business's master listing
    SELECT
        e.business_id AS business_id, e.profile AS profile, e.country AS country, e.validation AS validation,
        e.company_type AS company_type, e.detected_brands AS detected_brands, e.has_ecommerce AS has_ecommerce,
        e.crawl_status AS crawl_status, e.contact_name AS contact_name, e.email AS email,
        e.phone_e164 AS phone_web, b.domain AS domain, b.subregion_code AS subregion_code,
        b.phone_e164 AS phone_google, b.reviews_count AS reviews_count
    FROM {{ ref('lgn_d_enrichment') }} AS e
    INNER JOIN {{ ref('lgn_d_business') }} AS b ON b.business_id = e.business_id
    WHERE e.validation IN ('VALID', 'REVIEW')
      AND e.business_id NOT IN (SELECT m.business_id FROM {{ ref('lgn_f_customer_match') }} AS m
                                WHERE m.outcome = 'CUSTOMER')
),
lead_countries AS (
    SELECT DISTINCT l.country AS country FROM leads AS l
),
param AS (
    -- config.param: the country's row, else the common one (country NULL)
    SELECT c.country AS country, p.parameter AS parameter, argMax(p.value, isNotNull(p.country)) AS value
    FROM lead_countries AS c
    CROSS JOIN {{ ref('lgn_a_run_parameter') }} AS p
    WHERE isNull(p.country) OR p.country = c.country
    GROUP BY c.country, p.parameter
),
param_pivot AS (
    SELECT
        p.country AS country,
        anyIf(p.value, p.parameter = 'review_reference_min_n')       AS min_n,
        anyIf(p.value, p.parameter = 'review_p75_provisional')        AS p75_provisional,
        anyIf(p.value, p.parameter = 'review_median_provisional')     AS median_provisional
    FROM param AS p
    GROUP BY p.country
),
review_base AS (
    -- common.compute_review_reference: VALID leads of the country not matched with a customer
    SELECT b.country AS country, toFloat64(b.reviews_count) AS reviews
    FROM {{ ref('lgn_d_business') }} AS b
    WHERE b.business_id IN (SELECT e.business_id FROM {{ ref('lgn_d_enrichment') }} AS e WHERE e.validation = 'VALID')
      AND b.business_id NOT IN (SELECT m.business_id FROM {{ ref('lgn_f_customer_match') }} AS m
                                WHERE m.outcome = 'CUSTOMER')
      AND isNotNull(b.reviews_count)
),
review_stats AS (
    -- scoring.percentile_inc (Excel's PERCENTILE.INC): linear interpolation at (n - 1) * q of the sorted values
    SELECT
        q.country AS country, q.n AS n,
        q.xs[toUInt64(floor((q.n - 1) * 0.75)) + 1]
          + (q.xs[toUInt64(ceil((q.n - 1) * 0.75)) + 1] - q.xs[toUInt64(floor((q.n - 1) * 0.75)) + 1])
            * ((q.n - 1) * 0.75 - floor((q.n - 1) * 0.75))                                         AS p75,
        q.xs[toUInt64(floor((q.n - 1) * 0.5)) + 1]
          + (q.xs[toUInt64(ceil((q.n - 1) * 0.5)) + 1] - q.xs[toUInt64(floor((q.n - 1) * 0.5)) + 1])
            * ((q.n - 1) * 0.5 - floor((q.n - 1) * 0.5))                                           AS median
    FROM (
        SELECT r.country AS country, count() AS n, arraySort(groupArray(r.reviews)) AS xs
        FROM review_base AS r
        GROUP BY r.country
    ) AS q
),
review_ref AS (
    -- scoring.review_reference: below review_reference_min_n leads, the provisional cut-offs
    SELECT
        c.country AS country,
        coalesce(s.n, 0) AS n,
        toUInt8(coalesce(s.n, 0) < coalesce(toFloat64OrNull(pp.min_n), 30)) AS is_provisional,
        if(is_provisional, coalesce(toFloat64OrNull(pp.p75_provisional), 34.8), round(s.p75, 4))       AS p75,
        if(is_provisional, coalesce(toFloat64OrNull(pp.median_provisional), 10.5), round(s.median, 4)) AS median
    FROM lead_countries AS c
    LEFT JOIN review_stats AS s ON s.country = c.country
    LEFT JOIN param_pivot AS pp ON pp.country = c.country
),
brand_scope AS (
    -- config.brands("COMPETITOR"): the country's own list if it has one, else the common list
    SELECT c.country AS country, b.brand AS brand, b.brand_group AS brand_group
    FROM lead_countries AS c
    CROSS JOIN {{ ref('lgn_a_brand') }} AS b
    WHERE b.owner = 'COMPETITOR'
      AND (b.country = c.country
           OR (isNull(b.country)
               AND c.country NOT IN (SELECT x.country FROM {{ ref('lgn_a_brand') }} AS x
                                     WHERE x.owner = 'COMPETITOR' AND isNotNull(x.country))))
),
brand_pick AS (
    -- scoring.bucket_brands: the best group (1 best) among the competitor brands found on the website
    SELECT d.business_id AS business_id, d.profile AS profile,
           min(bs.brand_group) AS brand_group, argMin(bs.brand, (bs.brand_group, bs.brand)) AS brand
    FROM (SELECT l.business_id AS business_id, l.profile AS profile, l.country AS country,
                 arrayJoin(l.detected_brands) AS brand
          FROM leads AS l) AS d
    INNER JOIN brand_scope AS bs ON bs.country = d.country AND bs.brand = d.brand
    WHERE bs.brand_group IN (1, 2, 3)
    GROUP BY d.business_id, d.profile
),
zone_pick AS (
    -- scoring.bucket_zone: the quadrant of the business's subregion (the country's row, else the common one)
    SELECT l.business_id AS business_id, l.profile AS profile, argMax(z.quadrant, isNotNull(z.country)) AS quadrant
    FROM leads AS l
    INNER JOIN {{ ref('lgn_a_strategic_zone') }} AS z ON z.admin_code = l.subregion_code
    WHERE isNull(z.country) OR z.country = l.country
    GROUP BY l.business_id, l.profile
),
shared_pick AS (
    -- a domain in biz_cfg_word_list SHARED_DOMAIN (site builders, social networks…) is not an own website
    SELECT l.business_id AS business_id, l.profile AS profile, toUInt8(count() > 0) AS is_shared
    FROM leads AS l
    INNER JOIN {{ ref('lgn_a_shared_domain') }} AS s ON s.domain = l.domain
    WHERE isNull(s.country) OR s.country = l.country
    GROUP BY l.business_id, l.profile
),
scored AS (
    SELECT
        l.business_id AS business_id, l.profile AS profile, l.country AS country, l.validation AS validation,
        l.company_type AS company_type, l.has_ecommerce AS has_ecommerce, l.crawl_status AS crawl_status,
        l.domain AS domain, l.subregion_code AS subregion_code, l.reviews_count AS reviews_count,
        notEmpty(ifNull(l.contact_name, ''))                                           AS has_person,
        notEmpty(ifNull(l.email, ''))                                                  AS has_email,
        notEmpty(ifNull(l.phone_web, '')) OR notEmpty(ifNull(l.phone_google, ''))      AS has_phone,
        bp.brand_group AS brand_group, bp.brand AS brand, zp.quadrant AS quadrant,
        ifNull(sp.is_shared, 0) AS is_shared, rr.p75 AS p75, rr.median AS median
    FROM leads AS l
    LEFT JOIN brand_pick AS bp ON bp.business_id = l.business_id AND bp.profile = l.profile
    LEFT JOIN zone_pick AS zp ON zp.business_id = l.business_id AND zp.profile = l.profile
    LEFT JOIN shared_pick AS sp ON sp.business_id = l.business_id AND sp.profile = l.profile
    LEFT JOIN review_ref AS rr ON rr.country = l.country
),
buckets AS (
    -- scoring.bucket_*: the six factors computed in one pass over `scored` (a UNION ALL of six scans makes
    -- ClickHouse recompute every CTE above six times), then one row per factor with ARRAY JOIN
    SELECT f.business_id AS business_id, f.profile AS profile, f.country AS country,
           fb.1 AS factor, fb.2 AS bucket_code, fb.3 AS input_value
    FROM (
        SELECT
            s.business_id AS business_id, s.profile AS profile, s.country AS country,
            [('COMPANY_TYPE',
              if(s.validation = 'REVIEW' OR ifNull(s.company_type, '') NOT IN ('BUILDER_INSTALLER_SHOP', 'MAINTENANCE_OR_PREFAB'),
                 'WEAK_UNVERIFIED', toString(s.company_type)),
              CAST(s.company_type AS Nullable(String))),
             ('BRANDS', if(isNull(s.brand_group), 'NONE', concat('GROUP_', toString(s.brand_group))),
              CAST(s.brand AS Nullable(String))),
             ('ZONE', toString(ifNull(s.quadrant, 'NONE')), CAST(s.subregion_code AS Nullable(String))),
             -- a website that does not answer on crawl day still counts as own website (DF.8); social pages do not
             ('DIGITAL', multiIf(s.has_ecommerce = 1, 'ECOMMERCE',
                                 notEmpty(ifNull(s.domain, '')) AND s.is_shared = 0
                                 AND ifNull(s.crawl_status, '') NOT IN ('NO_WEBSITE', 'SOCIAL_ONLY'), 'OWN_WEBSITE', 'NONE'),
              CAST(s.crawl_status AS Nullable(String))),
             ('CONTACT', multiIf(s.has_person AND s.has_email AND s.has_phone, 'PERSON_EMAIL_PHONE',
                                 s.has_email AND s.has_phone, 'EMAIL_PHONE',
                                 s.has_person OR s.has_email OR s.has_phone, 'ANY', 'NONE'),
              CAST(NULL AS Nullable(String))),
             -- strict: more than P75, more than the median, at least one; 0 or no data -> NONE
             ('REVIEWS', multiIf(ifNull(s.reviews_count, 0) = 0, 'NONE', s.reviews_count > s.p75, 'ABOVE_P75',
                                 s.reviews_count > s.median, 'ABOVE_MEDIAN', 'ANY'),
              CAST(if(isNull(s.reviews_count), 'no data', toString(s.reviews_count)) AS Nullable(String)))
            ] AS fbs
        FROM scored AS s
    ) AS f
    ARRAY JOIN f.fbs AS fb
)
SELECT
    bk.business_id AS business_id, bk.country AS country, bk.profile AS profile, bk.factor AS factor,
    bk.bucket_code AS bucket_code, bk.input_value AS input_value,
    -- config.scoped: country + profile > country > profile > neither
    argMax(r.points, isNotNull(r.country) * 2 + isNotNull(r.profile)) AS points
FROM buckets AS bk
INNER JOIN {{ ref('lgn_a_scoring_rule') }} AS r ON r.factor = bk.factor AND r.bucket_code = bk.bucket_code
WHERE (isNull(r.country) OR r.country = bk.country) AND (isNull(r.profile) OR r.profile = bk.profile)
GROUP BY bk.business_id, bk.country, bk.profile, bk.factor, bk.bucket_code, bk.input_value
SETTINGS join_use_nulls = 1
