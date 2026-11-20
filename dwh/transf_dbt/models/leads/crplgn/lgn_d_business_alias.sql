{{ config(materialized='view') }}
-- Business absorbed in a merge -> the surviving one (DT.4.2). Validations, exclusions and Salesforce
-- results follow the alias. One level here; the engine follows chains (identity.resolve_alias).
SELECT
    a.old_id                          AS old_id,
    argMax(a.surviving_id, a.inserted_at) AS surviving_id,
    argMax(a.reason, a.inserted_at)   AS reason,
    max(a.inserted_at)                AS aliased_at
FROM {{ source('engine', 'business_alias_out') }} AS a
GROUP BY a.old_id
