{{ config(materialized='view') }}
-- Official boundaries loaded (no geometry): the engine resolves the subregion in Python (geo.py).
SELECT
    g.country                    AS country,
    g.admin_level                AS admin_level,
    g.admin_code                 AS admin_code,
    g.admin_name                 AS admin_name,
    g.parent_code                AS parent_code,
    g.source                     AS source,
    g.version                    AS version,
    g.loaded_at                  AS loaded_at
FROM {{ source('landing', 'reference_geo') }} AS g
