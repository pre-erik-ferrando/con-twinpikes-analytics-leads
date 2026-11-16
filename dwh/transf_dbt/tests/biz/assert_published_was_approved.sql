-- Only leads approved by Sales Ops reach an export (DF.9).
SELECT e.business_id, e.export_id
FROM {{ ref('lgn_f_export_lead') }} AS e
LEFT JOIN {{ ref('lgn_f_validation') }} AS v ON v.business_id = e.business_id AND v.profile = e.profile
WHERE e.status IN ('GENERATED', 'UPLOADED') AND coalesce(v.decision, '') NOT IN ('ACCEPTED')
  AND e.business_id NOT IN (SELECT business_id FROM {{ ref('lgn_f_validation') }} WHERE decision = 'EXCLUDED')
SETTINGS join_use_nulls = 1
