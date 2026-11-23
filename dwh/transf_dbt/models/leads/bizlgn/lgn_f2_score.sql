{{ config(materialized='view') }}
-- The profile score computed in SQL, per lead: total (max 37.5), Rating and breakdown in the cockpit's order
-- (type, brands, zone, digital, contact, reviews). Mirror of scoring.score + scoring.rate, alongside the
-- engine's lgn_f_score and checked equal to it in tests_db; nothing reads it yet.
WITH totals AS (
    SELECT
        c.business_id AS business_id, c.country AS country, c.profile AS profile,
        round(toFloat64(sum(c.points)), 2) AS total,
        [toFloat64(sumIf(c.points, c.factor = 'COMPANY_TYPE')), toFloat64(sumIf(c.points, c.factor = 'BRANDS')),
         toFloat64(sumIf(c.points, c.factor = 'ZONE')), toFloat64(sumIf(c.points, c.factor = 'DIGITAL')),
         toFloat64(sumIf(c.points, c.factor = 'CONTACT')), toFloat64(sumIf(c.points, c.factor = 'REVIEWS'))] AS breakdown,
        count() AS n_factors
    FROM {{ ref('lgn_f2_score_component') }} AS c
    GROUP BY c.business_id, c.country, c.profile
),
band_scope AS (
    -- the country's thresholds, else the common ones (country NULL)
    SELECT t.country AS country, b.rating AS rating, toFloat64(argMax(b.min_score, isNotNull(b.country))) AS min_score
    FROM (SELECT DISTINCT country FROM totals) AS t
    CROSS JOIN {{ ref('lgn_a_rating_band') }} AS b
    WHERE isNull(b.country) OR b.country = t.country
    GROUP BY t.country, b.rating
)
SELECT
    t.business_id AS business_id, t.country AS country, t.profile AS profile, t.total AS total,
    -- scoring.rate: the highest threshold reached (>=), else the lowest band
    if(countIf(t.total >= bs.min_score - 1e-9) > 0,
       argMaxIf(bs.rating, bs.min_score, t.total >= bs.min_score - 1e-9),
       argMin(bs.rating, bs.min_score))                                    AS rating,
    any(t.breakdown) AS breakdown,
    any(t.n_factors) AS n_factors
FROM totals AS t
INNER JOIN band_scope AS bs ON bs.country = t.country
GROUP BY t.business_id, t.country, t.profile, t.total
