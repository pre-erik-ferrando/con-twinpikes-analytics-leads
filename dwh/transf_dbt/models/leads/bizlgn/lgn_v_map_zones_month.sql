{{ config(materialized='view') }}
-- Map 3 by month (DF.13, DT.8.4): per subregion and status month, how many of its published leads are
-- converted, working, nurturing or discarded in Salesforce. The subregion is the lead's own (the
-- file's is only a check).
SELECT
    l.des_country                                AS pk_country,
    l.des_subregion_code                         AS pk_subregion_code,
    s.status_month                               AS pk_status_month,
    any(l.des_subregion)                         AS des_subregion,
    any(l.des_quadrant)                          AS des_quadrant,
    countIf(s.status = 'CONVERTED')              AS val_converted,
    countIf(s.status = 'WORKING')                AS val_working,
    countIf(s.status = 'NURTURING')              AS val_nurturing,
    countIf(s.status = 'DISCARDED')              AS val_discarded
FROM {{ ref('lgn_d_sf_status') }} AS s
INNER JOIN (
    SELECT DISTINCT pk_business_id, des_country, des_subregion_code, des_subregion, des_quadrant
    FROM {{ ref('lgn_v_lead') }}
) AS l ON l.pk_business_id = s.business_id
GROUP BY l.des_country, l.des_subregion_code, s.status_month
