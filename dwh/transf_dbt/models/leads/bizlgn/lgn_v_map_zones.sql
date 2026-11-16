{{ config(materialized='view') }}
-- Maps 1 and 3: subregions by quadrant, whether they have been searched, and what happened to their
-- published leads once the call results are loaded (DF.13).
WITH searched AS (
    SELECT q.area_id AS area_id, count() AS n_queries, max(q.inserted_at) AS last_searched_at
    FROM {{ source('ops', 'search_query_log') }} AS q
    GROUP BY q.area_id
),
leads AS (
    SELECT l.des_country AS country, l.des_subregion_code AS subregion_code,
           count()                                                              AS n_leads,
           countIf(l.des_status = 'Published')                                  AS n_published,
           countIf(l.des_cockpit_status IN ('WORKING', 'NURTURING'))            AS n_in_progress,
           countIf(l.des_cockpit_status = 'NOT_QUALIFIED')                      AS n_not_qualified
    FROM {{ ref('lgn_v_lead') }} AS l
    GROUP BY l.des_country, l.des_subregion_code
)
SELECT
    a.country                                    AS pk_country,
    a.area_id                                    AS pk_area_id,
    a.admin_code                                 AS des_subregion_code,
    a.area_name                                  AS des_subregion,
    a.region_name                                AS des_region,
    z.quadrant                                   AS des_quadrant,
    toUInt8(isNotNull(s.area_id))                AS val_is_searched,
    s.n_queries                                  AS val_queries,
    s.last_searched_at                           AS dt_last_searched,
    coalesce(ld.n_leads, 0)                      AS val_leads,
    coalesce(ld.n_published, 0)                  AS val_published,
    coalesce(ld.n_in_progress, 0)                AS val_in_progress,
    coalesce(ld.n_not_qualified, 0)              AS val_not_qualified
FROM {{ ref('lgn_a_search_area') }} AS a
LEFT JOIN {{ ref('lgn_a_strategic_zone') }} AS z ON z.country = a.country AND z.admin_code = a.admin_code
LEFT JOIN searched AS s ON s.area_id = a.area_id
LEFT JOIN leads AS ld ON ld.country = a.country AND ld.subregion_code = a.admin_code
WHERE a.level = 'SUBREGION'
SETTINGS join_use_nulls = 1
