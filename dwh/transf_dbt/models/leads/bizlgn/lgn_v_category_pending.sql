{{ config(materialized='view') }}
-- Google categories seen and not classified yet, with the number of listings that have them (DT.4.4.4).
-- They are classified in Configuration as INCLUDE, CONDITIONAL or EXCLUDE and count from the next run.
-- The language is the default one of the listing's subregion (Google names categories in it); a rule
-- without language or country applies to all, and a listing with no known language takes every rule
-- (config.category_rules). The category is normalized as normalize.normalize_category
-- does (lower case, no diacritics, spaces collapsed), which is how biz_cfg_category_rule stores it.
WITH seen AS (
    SELECT
        p.place_id AS place_id, p.country AS country, p.first_seen_at AS first_seen_at,
        p.last_seen_at AS last_seen_at, a.language AS language, c AS category,
        if(lowerUTF8(trimBoth(c)) IN ('', 'no website', 'no web', 'not checked', '#n/a', 'n/a', 'xxx',
                                      'not_applicable', 'unknown', 'none', 'null', '-'),
           '',
           arrayStringConcat(splitByWhitespace(replaceRegexpAll(normalizeUTF8NFKD(lowerUTF8(c)), '\\p{Mn}', '')),
                             ' '))                                                   AS category_normalized
    FROM {{ ref('lgn_d_place') }} AS p
    ARRAY JOIN p.categories AS c
    LEFT JOIN (
        -- common.subregion_languages: the first of the subregion's default languages
        SELECT s.country AS country, s.admin_code AS admin_code,
               any(trimBoth(splitByChar(',', replaceAll(s.default_language, '/', ','))[1])) AS language
        FROM {{ ref('lgn_a_search_area') }} AS s
        WHERE s.level = 'SUBREGION' AND notEmpty(ifNull(s.default_language, ''))
        GROUP BY s.country, s.admin_code
    ) AS a ON a.country = p.country AND a.admin_code = p.subregion_code
),
classified AS (
    SELECT DISTINCT s.place_id AS place_id, s.category_normalized AS category_normalized
    FROM seen AS s
    INNER JOIN {{ ref('lgn_a_category_rule') }} AS r ON r.category_normalized = s.category_normalized
    WHERE (isNull(r.country) OR r.country = s.country) AND (isNull(r.language) OR isNull(s.language) OR r.language = s.language)
)
SELECT
    s.country                                AS des_country,
    s.language                               AS des_language,
    s.category_normalized                    AS pk_category_normalized,
    any(s.category)                          AS des_category,
    uniqExact(s.place_id)                    AS val_listings,
    min(s.first_seen_at)                     AS dt_first_seen,
    max(s.last_seen_at)                      AS dt_last_seen
FROM seen AS s
WHERE notEmpty(s.category_normalized)
  AND (s.place_id, s.category_normalized) NOT IN (SELECT c.place_id, c.category_normalized FROM classified AS c)
GROUP BY s.country, s.language, s.category_normalized
SETTINGS join_use_nulls = 1
