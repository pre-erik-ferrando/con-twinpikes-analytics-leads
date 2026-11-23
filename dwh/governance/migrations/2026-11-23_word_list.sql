-- 2026-11-23 (after 2026-11-23_fewer_tables.sql): 51 -> 47 tables. Hand-run, with no run in progress.
--   biz_cfg_name_stopword, biz_cfg_legal_form, biz_cfg_pool_signal, biz_cfg_shared_domain,
--   biz_cfg_page_priority -> biz_cfg_word_list (list_name NAME_STOPWORD, LEGAL_FORM, POOL_SIGNAL,
--   SHARED_DOMAIN, PAGE_PRIORITY), every row with its history (inserted_at, updated_by, active).

-- ============================================================ 1. python leadgen.py init   (creates biz_cfg_word_list)

-- ============================================================ 2. copy the rows
INSERT INTO fld_bizlgn.biz_cfg_word_list
    (pk_country, pk_profile, pk_language, pk_list_name, pk_value, des_kind, val_priority, val_active, updated_by, inserted_at)
SELECT pk_country, NULL, pk_language, 'NAME_STOPWORD', pk_word, NULL, NULL, val_active, updated_by, inserted_at
FROM fld_bizlgn.biz_cfg_name_stopword;

INSERT INTO fld_bizlgn.biz_cfg_word_list
    (pk_country, pk_profile, pk_language, pk_list_name, pk_value, des_kind, val_priority, val_active, updated_by, inserted_at)
SELECT pk_country, NULL, NULL, 'LEGAL_FORM', pk_form_normalized, NULL, NULL, val_active, updated_by, inserted_at
FROM fld_bizlgn.biz_cfg_legal_form;

INSERT INTO fld_bizlgn.biz_cfg_word_list
    (pk_country, pk_profile, pk_language, pk_list_name, pk_value, des_kind, val_priority, val_active, updated_by, inserted_at)
SELECT pk_country, pk_profile, pk_language, 'POOL_SIGNAL', pk_stem, NULL, NULL, val_active, updated_by, inserted_at
FROM fld_bizlgn.biz_cfg_pool_signal;

INSERT INTO fld_bizlgn.biz_cfg_word_list
    (pk_country, pk_profile, pk_language, pk_list_name, pk_value, des_kind, val_priority, val_active, updated_by, inserted_at)
SELECT pk_country, NULL, NULL, 'SHARED_DOMAIN', pk_domain, des_kind, NULL, val_active, updated_by, inserted_at
FROM fld_bizlgn.biz_cfg_shared_domain;

INSERT INTO fld_bizlgn.biz_cfg_word_list
    (pk_country, pk_profile, pk_language, pk_list_name, pk_value, des_kind, val_priority, val_active, updated_by, inserted_at)
SELECT pk_country, NULL, pk_language, 'PAGE_PRIORITY', pk_pattern, NULL, val_priority, val_active, updated_by, inserted_at
FROM fld_bizlgn.biz_cfg_page_priority;

-- check: the two numbers must be equal before part 3
SELECT (SELECT count() FROM fld_bizlgn.biz_cfg_word_list),
       (SELECT count() FROM fld_bizlgn.biz_cfg_name_stopword) + (SELECT count() FROM fld_bizlgn.biz_cfg_legal_form)
       + (SELECT count() FROM fld_bizlgn.biz_cfg_pool_signal) + (SELECT count() FROM fld_bizlgn.biz_cfg_shared_domain)
       + (SELECT count() FROM fld_bizlgn.biz_cfg_page_priority);

-- ============================================================ 3. python leadgen.py dbt run, then drop the old tables
DROP VIEW IF EXISTS fld_bizlgn.biz_cfg_name_stopword_current;
DROP VIEW IF EXISTS fld_bizlgn.biz_cfg_legal_form_current;
DROP VIEW IF EXISTS fld_bizlgn.biz_cfg_pool_signal_current;
DROP VIEW IF EXISTS fld_bizlgn.biz_cfg_shared_domain_current;
DROP VIEW IF EXISTS fld_bizlgn.biz_cfg_page_priority_current;
DROP TABLE IF EXISTS fld_bizlgn.biz_cfg_name_stopword;
DROP TABLE IF EXISTS fld_bizlgn.biz_cfg_legal_form;
DROP TABLE IF EXISTS fld_bizlgn.biz_cfg_pool_signal;
DROP TABLE IF EXISTS fld_bizlgn.biz_cfg_shared_domain;
DROP TABLE IF EXISTS fld_bizlgn.biz_cfg_page_priority;
