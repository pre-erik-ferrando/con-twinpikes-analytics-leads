-- 2026-11-23: 64 -> 51 tables. Hand-run, in three parts, on a database where no run is in progress.
--   customer_extract_* / sf_status_* / import_list_*  -> landing.upload_file + landing.upload_row (kind)
--   landing.gmaps_run                                 -> columns of ops.search_query_log
--   landing.landing_meta                              -> dropped (nothing read it)
--   ops.suppression_list                              -> dropped (the engine reads biz_suppression_request)
--   biz.export_score_out                              -> columns of biz.export_lead_out
--   biz.business_alias_out + biz.merge_suspect_out    -> biz.business_identity_out (both empty today)
--   biz.category_seen_out, exclusion_out, sf_status_out -> dropped: read from their sources (dbt views
--                                                        lgn_v_category_pending, lgn_f_exclusion, lgn_d_sf_status)
--   biz.rule_evaluation_out                           -> dropped: the filter is the dbt view lgn_f_rule_evaluation;
--                                                        place_norm_out gains the normalized texts it reads

-- ============================================================ 1. before `python leadgen.py init`
RENAME TABLE fld_opslgn.search_query_log TO fld_opslgn.search_query_log_old;
DROP TABLE IF EXISTS fld_bizlgn.export_lead_out;       -- empty (no export yet); init recreates it with the score
ALTER TABLE fld_bizlgn.place_norm_out
    ADD COLUMN IF NOT EXISTS name_norm String DEFAULT '' AFTER scraped_at,
    ADD COLUMN IF NOT EXISTS owner_description_norm String DEFAULT '' AFTER name_norm,
    ADD COLUMN IF NOT EXISTS categories_norm Array(String) DEFAULT [] AFTER owner_description_norm;

-- then:  python leadgen.py init   (creates upload_file, upload_row, business_identity_out, the new search_query_log
--         and export_lead_out)

-- ============================================================ 2. copy the data
INSERT INTO fld_landinglgn.upload_file
    (file_id, kind, country, subsidiary_code, source_system, extract_date, file_name, loaded_by, loaded_at)
SELECT extract_id, 'CUSTOMER_EXTRACT', country, subsidiary_code, source_system, extract_date, file_name, loaded_by,
       loaded_at
FROM fld_landinglgn.customer_extract_file;

INSERT INTO fld_landinglgn.upload_row (file_id, source_row_id, row_json, loaded_at)
SELECT extract_id, source_row_id, row_json, loaded_at FROM fld_landinglgn.customer_extract_row;

INSERT INTO fld_landinglgn.upload_file (file_id, kind, status_month, file_name, loaded_by, loaded_at)
SELECT status_file_id, 'SF_STATUS', status_month, file_name, loaded_by, loaded_at FROM fld_landinglgn.sf_status_file;

INSERT INTO fld_landinglgn.upload_row (file_id, source_row_id, row_json, loaded_at)
SELECT status_file_id, source_row_id, row_json, loaded_at FROM fld_landinglgn.sf_status_row;

INSERT INTO fld_landinglgn.upload_file (file_id, kind, country, profile, file_name, loaded_by, loaded_at)
SELECT import_file_id, 'IMPORT_LIST', country, profile, file_name, loaded_by, loaded_at
FROM fld_landinglgn.import_list_file;

INSERT INTO fld_landinglgn.upload_row (file_id, source_row_id, row_json, loaded_at)
SELECT import_file_id, source_row_id, row_json, loaded_at FROM fld_landinglgn.import_list_row;

INSERT INTO fld_opslgn.search_query_log
    (run_id, query_id, capture_batch_id, country, profile, term, area_id, language, max_places, pass_no, n_results,
     n_new, is_saturated, is_saturated_at_leaf, apify_run_id, actor_build, input_json, status, cost_usd, started_at,
     finished_at, inserted_at)
SELECT q.run_id, q.query_id, q.capture_batch_id, q.country, q.profile, q.term, q.area_id, q.language, q.max_places,
       q.pass_no, q.n_results, q.n_new, q.is_saturated, q.is_saturated_at_leaf, q.apify_run_id, g.actor_build,
       g.input_json, g.status, toFloat64OrNull(g.cost_usd), g.started_at, g.finished_at, q.inserted_at
FROM fld_opslgn.search_query_log_old AS q
LEFT JOIN fld_landinglgn.gmaps_run AS g ON g.query_id = q.query_id
SETTINGS join_use_nulls = 1;

-- check: each pair of numbers must be equal before part 3
SELECT 'files', (SELECT count() FROM fld_landinglgn.upload_file),
       (SELECT count() FROM fld_landinglgn.customer_extract_file) + (SELECT count() FROM fld_landinglgn.sf_status_file)
       + (SELECT count() FROM fld_landinglgn.import_list_file)
UNION ALL
SELECT 'rows', (SELECT count() FROM fld_landinglgn.upload_row),
       (SELECT count() FROM fld_landinglgn.customer_extract_row) + (SELECT count() FROM fld_landinglgn.sf_status_row)
       + (SELECT count() FROM fld_landinglgn.import_list_row)
UNION ALL
SELECT 'queries', (SELECT count() FROM fld_opslgn.search_query_log), (SELECT count() FROM fld_opslgn.search_query_log_old)
UNION ALL
SELECT 'queries with actor input', (SELECT countIf(isNotNull(input_json)) FROM fld_opslgn.search_query_log),
       (SELECT count() FROM fld_landinglgn.gmaps_run);

-- ============================================================ 3. drop the old tables, then:
--   python leadgen.py dbt run
--   python leadgen.py renormalize --country AT      (the filter's texts of the listings already captured)
DROP TABLE IF EXISTS fld_landinglgn.customer_extract_file;
DROP TABLE IF EXISTS fld_landinglgn.customer_extract_row;
DROP TABLE IF EXISTS fld_landinglgn.sf_status_file;
DROP TABLE IF EXISTS fld_landinglgn.sf_status_row;
DROP TABLE IF EXISTS fld_landinglgn.import_list_file;
DROP TABLE IF EXISTS fld_landinglgn.import_list_row;
DROP TABLE IF EXISTS fld_landinglgn.gmaps_run;
DROP TABLE IF EXISTS fld_landinglgn.landing_meta;
DROP TABLE IF EXISTS fld_opslgn.search_query_log_old;
DROP TABLE IF EXISTS fld_opslgn.suppression_list;
DROP TABLE IF EXISTS fld_bizlgn.export_score_out;
DROP TABLE IF EXISTS fld_bizlgn.business_alias_out;
DROP TABLE IF EXISTS fld_bizlgn.merge_suspect_out;
DROP TABLE IF EXISTS fld_bizlgn.category_seen_out;
DROP TABLE IF EXISTS fld_bizlgn.exclusion_out;
DROP TABLE IF EXISTS fld_bizlgn.sf_status_out;
DROP TABLE IF EXISTS fld_bizlgn.rule_evaluation_out;
