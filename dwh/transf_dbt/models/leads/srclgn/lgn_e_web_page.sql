{{ config(materialized='view') }}
-- Downloaded pages without their HTML (which stays compressed in landing.web_page).
SELECT
    p.crawl_id                              AS crawl_id,
    c.domain                                AS domain,
    p.url                                   AS url,
    toUInt8OrNull(p.depth)                  AS depth,
    toUInt16OrNull(p.status_code)           AS status_code,
    p.content_hash                          AS content_hash,
    length(coalesce(p.html, ''))            AS html_bytes,
    p.loaded_at                             AS loaded_at
FROM {{ source('landing', 'web_page') }} AS p
INNER JOIN {{ source('landing', 'web_crawl') }} AS c ON c.crawl_id = p.crawl_id
