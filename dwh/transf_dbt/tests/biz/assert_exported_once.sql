-- A lead is exported once: at most one export of a lead in GENERATED or UPLOADED (DT.8.5).
SELECT business_id, count() AS n
FROM {{ ref('lgn_f_export_lead') }}
WHERE status IN ('GENERATED', 'UPLOADED')
GROUP BY business_id
HAVING n > 1
