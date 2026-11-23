{{ config(materialized='view') }}
-- The relevance filter (DT.4.4), the only implementation: per business and profile of the capture batches
-- (aliases resolved), with the CURRENT configuration. The engine reads it (store.filter_decisions) right
-- after writing the batch; tests_db checks it against the Python reference (tests/filter_reference.py).
-- Order of evaluation (DT.4.4.1), first match decides:
--  1. permanently closed (every listing)                    -> EXCLUDE CLOSED
--  2. suppressed (business, listing, phone or domain) or     -> EXCLUDE SUPPRESSED / PREVIOUSLY_EXCLUDED
--     permanently excluded (lgn_f_exclusion)
--  3. HARD title rule                                        -> EXCLUDE TITLE_RULE (its reason)
--  4. any INCLUDE category                                   -> INCLUDE (beats category exclusion)
--  5. any CONDITIONAL category + a pool word at the start of a word of the title, else of the owner's
--     description (HVAC needs no pool word)                  -> INCLUDE
--  6. SOFT title rule                                        -> EXCLUDE TITLE_RULE
--  7. any EXCLUDE category                                   -> EXCLUDE CATEGORY_RULE
--  8. nothing fired                                          -> EXCLUDE DEFAULT_EXCLUDE
-- Texts arrive normalized from the engine (place_norm_out: name_norm, owner_description_norm,
-- categories_norm) as the rules are stored. When several categories or rules fire, the first in
-- alphabetical order is reported. Rules apply by country and profile (the most specific row of each key
-- wins); a category rule of the subregion's language beats a common one.
WITH leads AS (
    SELECT DISTINCT coalesce(al.surviving_id, cbl.business_id) AS business_id, cbl.country AS country,
                    cbl.profile AS profile
    FROM {{ source('ops', 'capture_batch_lead') }} AS cbl
    LEFT JOIN {{ ref('lgn_d_business_alias') }} AS al ON al.old_id = cbl.business_id
),
languages AS (
    -- common.subregion_languages: the first of the subregion's default languages
    SELECT s.country AS country, s.admin_code AS admin_code,
           any(trimBoth(splitByChar(',', replaceAll(s.default_language, '/', ','))[1])) AS language
    FROM {{ ref('lgn_a_search_area') }} AS s
    WHERE s.level = 'SUBREGION' AND notEmpty(ifNull(s.default_language, ''))
    GROUP BY s.country, s.admin_code
),
biz AS (
    SELECT
        l.business_id AS business_id, l.country AS country, l.profile AS profile, lg.language AS language,
        b.name_norm AS name_norm, b.owner_description_norm AS owner_description_norm,
        b.categories_norm AS categories_norm, b.is_closed_permanently AS is_closed_permanently,
        b.is_closed_temporarily AS is_closed_temporarily, b.phones AS phones, b.domains AS domains,
        b.place_ids AS place_ids
    FROM leads AS l
    INNER JOIN {{ ref('lgn_d_business') }} AS b ON b.business_id = l.business_id
    LEFT JOIN languages AS lg ON lg.country = l.country AND lg.admin_code = b.subregion_code
),
suppression AS (
    SELECT
        groupUniqArray(sr.val_business_id) AS business_ids, groupUniqArray(sr.des_place_id) AS place_ids,
        groupUniqArray(sr.des_phone) AS phones, groupUniqArray(sr.des_domain) AS domains
    FROM {{ source('writeback', 'biz_suppression_request') }} AS sr
),
exclusion AS (
    -- state.permanent_exclusions: Sales Ops first, then Salesforce
    SELECT x.business_id AS business_id, x.profile AS profile, x.scope AS scope,
           concat(x.origin, ':', x.reason) AS label, x.origin <> 'HUMAN' AS rank
    FROM {{ ref('lgn_f_exclusion') }} AS x
),
previous AS (
    SELECT b.business_id AS business_id, b.profile AS profile, argMin(x.label, x.rank) AS label
    FROM biz AS b
    INNER JOIN exclusion AS x ON x.business_id = b.business_id
    WHERE x.scope = 'ALL' OR x.profile = b.profile
    GROUP BY b.business_id, b.profile
),
category AS (
    -- config.category_rules: per category, a rule of the language beats a common one; then the most
    -- specific of country and profile
    SELECT b.business_id AS business_id, b.profile AS profile, c AS category,
           argMax(r.classification, (isNotNull(r.language), isNotNull(r.country) * 2 + isNotNull(r.profile)))
               AS classification
    FROM biz AS b
    ARRAY JOIN b.categories_norm AS c
    INNER JOIN {{ ref('lgn_a_category_rule') }} AS r ON r.category_normalized = c
    WHERE (isNull(r.country) OR r.country = b.country) AND (isNull(r.profile) OR r.profile = b.profile)
      AND (isNull(b.language) OR isNull(r.language) OR r.language = b.language)
    GROUP BY b.business_id, b.profile, c
),
categories AS (
    SELECT c.business_id AS business_id, c.profile AS profile,
           minIf(c.category, c.classification = 'INCLUDE')     AS included,
           minIf(c.category, c.classification = 'CONDITIONAL') AS conditional,
           minIf(c.category, c.classification = 'EXCLUDE')     AS excluded
    FROM category AS c
    GROUP BY c.business_id, c.profile
),
title AS (
    -- config.scoped: per pattern, the most specific row of country and profile; filtering.title_matches
    SELECT t.business_id AS business_id, t.profile AS profile, t.pattern_normalized AS pattern_normalized,
           t.pattern AS pattern, t.rule_class AS rule_class, t.reason_code AS reason_code,
           notEmpty(t.pattern_normalized) AND multiIf(
               t.match_type = 'SUBSTRING', position(t.name_norm, t.pattern_normalized) > 0,
               t.match_type = 'PREFIX', position(concat(' ', t.name_norm, ' '), concat(' ', t.pattern_normalized)) > 0,
               position(concat(' ', t.name_norm, ' '), concat(' ', t.pattern_normalized, ' ')) > 0) AS matched
    FROM (
        SELECT b.business_id AS business_id, b.profile AS profile, b.name_norm AS name_norm,
               r.pattern_normalized AS pattern_normalized,
               argMax(r.pattern, isNotNull(r.country) * 2 + isNotNull(r.profile))                   AS pattern,
               argMax(ifNull(r.match_type, 'TOKEN'), isNotNull(r.country) * 2 + isNotNull(r.profile)) AS match_type,
               argMax(r.rule_class, isNotNull(r.country) * 2 + isNotNull(r.profile))                AS rule_class,
               argMax(r.reason_code, isNotNull(r.country) * 2 + isNotNull(r.profile))               AS reason_code
        FROM biz AS b
        CROSS JOIN {{ ref('lgn_a_title_rule') }} AS r
        WHERE (isNull(r.country) OR r.country = b.country) AND (isNull(r.profile) OR r.profile = b.profile)
        GROUP BY b.business_id, b.profile, b.name_norm, r.pattern_normalized
    ) AS t
),
titles AS (
    SELECT t.business_id AS business_id, t.profile AS profile,
           countIf(t.matched AND t.rule_class = 'HARD') > 0                        AS hard,
           argMinIf(t.reason_code, t.pattern_normalized, t.matched AND t.rule_class = 'HARD') AS hard_reason,
           argMinIf(t.pattern, t.pattern_normalized, t.matched AND t.rule_class = 'HARD')     AS hard_pattern,
           countIf(t.matched AND t.rule_class = 'SOFT') > 0                        AS soft,
           argMinIf(t.reason_code, t.pattern_normalized, t.matched AND t.rule_class = 'SOFT') AS soft_reason,
           argMinIf(t.pattern, t.pattern_normalized, t.matched AND t.rule_class = 'SOFT')     AS soft_pattern
    FROM title AS t
    GROUP BY t.business_id, t.profile
),
pool AS (
    -- filtering.has_pool_signal: the first stem (alphabetical) at the start of a word of the title, else
    -- of the owner's description; config.pool_stems takes the stems of every language
    SELECT b.business_id AS business_id, b.profile AS profile,
           arraySort(groupUniqArray(s.stem)) AS stems,
           any(b.name_norm) AS name_norm, any(b.owner_description_norm) AS owner_description_norm
    FROM biz AS b
    CROSS JOIN {{ ref('lgn_a_pool_signal') }} AS s
    WHERE (isNull(s.country) OR s.country = b.country) AND (isNull(s.profile) OR s.profile = b.profile)
    GROUP BY b.business_id, b.profile
),
pool_hit AS (
    SELECT p.business_id AS business_id, p.profile AS profile,
           if(notEmpty(arrayFirst(st -> arrayExists(w -> startsWith(w, st), splitByWhitespace(p.name_norm)), p.stems)),
              arrayFirst(st -> arrayExists(w -> startsWith(w, st), splitByWhitespace(p.name_norm)), p.stems),
              arrayFirst(st -> arrayExists(w -> startsWith(w, st), splitByWhitespace(p.owner_description_norm)),
                         p.stems)) AS stem
    FROM pool AS p
),
decided AS (
    SELECT
        b.business_id AS business_id, b.country AS country, b.profile AS profile,
        b.is_closed_temporarily AS is_closed_temporarily,
        multiIf(b.is_closed_permanently = 1, 1,
                has(sp.business_ids, b.business_id) OR hasAny(sp.place_ids, b.place_ids)
                    OR hasAny(sp.phones, b.phones) OR hasAny(sp.domains, b.domains), 2,
                notEmpty(ifNull(pv.label, '')), 3,
                ifNull(t.hard, 0), 4,
                notEmpty(ifNull(c.included, '')), 5,
                notEmpty(ifNull(c.conditional, '')) AND b.profile = 'HVAC', 6,
                notEmpty(ifNull(c.conditional, '')) AND notEmpty(ifNull(ph.stem, '')), 7,
                ifNull(t.soft, 0), 8,
                notEmpty(ifNull(c.excluded, '')), 9,
                10) AS step,
        pv.label AS previous, t.hard_reason AS hard_reason, t.hard_pattern AS hard_pattern,
        t.soft_reason AS soft_reason, t.soft_pattern AS soft_pattern, c.included AS included,
        c.conditional AS conditional, c.excluded AS excluded, ph.stem AS stem
    FROM biz AS b
    CROSS JOIN suppression AS sp
    LEFT JOIN previous AS pv ON pv.business_id = b.business_id AND pv.profile IS NOT DISTINCT FROM b.profile
    LEFT JOIN titles AS t ON t.business_id = b.business_id AND t.profile IS NOT DISTINCT FROM b.profile
    LEFT JOIN categories AS c ON c.business_id = b.business_id AND c.profile IS NOT DISTINCT FROM b.profile
    LEFT JOIN pool_hit AS ph ON ph.business_id = b.business_id AND ph.profile IS NOT DISTINCT FROM b.profile
)
SELECT
    d.business_id AS business_id, d.country AS country, d.profile AS profile,
    if(d.step IN (5, 6, 7), 'INCLUDE', 'EXCLUDE') AS decision,
    CAST(multiIf(d.step = 1, 'CLOSED', d.step = 2, 'SUPPRESSED', d.step = 3, 'PREVIOUSLY_EXCLUDED',
                 d.step IN (4, 8), 'TITLE_RULE', d.step = 9, 'CATEGORY_RULE', d.step = 10, 'DEFAULT_EXCLUDE', NULL)
         AS Nullable(String)) AS stage,
    CAST(multiIf(d.step = 1, 'CLOSED_PERMANENTLY', d.step = 2, 'SUPPRESSION_LIST', d.step = 3, d.previous,
                 d.step = 4, d.hard_reason, d.step = 5, 'CATEGORY_INCLUDE', d.step = 6, 'CATEGORY_CONDITIONAL',
                 d.step = 7, 'CATEGORY_CONDITIONAL_POOL_SIGNAL', d.step = 8, d.soft_reason,
                 d.step = 9, 'CATEGORY_EXCLUDE', 'NO_RULE_FIRED') AS Nullable(String)) AS rule,
    CAST(multiIf(d.step = 4, d.hard_pattern, d.step = 5, d.included, d.step = 6, d.conditional,
                 d.step = 7, concat(d.conditional, ' + ', d.stem), d.step = 8, d.soft_pattern,
                 d.step = 9, d.excluded, NULL) AS Nullable(String)) AS trigger_value,
    toUInt8(d.is_closed_temporarily) AS is_closed_temporarily
FROM decided AS d
SETTINGS join_use_nulls = 1
