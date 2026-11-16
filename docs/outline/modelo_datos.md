# Modelo de datos

El contrato de cada tabla que escriben el motor o Intelek está en un solo sitio,
[`ddl.py`](../../dwh/transf_py/leadgen/ddl.py); las fuentes de dbt se generan de él. Nombres lógicos en el
código; en las tablas que lee o escribe Intelek (`biz_cfg_*`, `biz_lead_validation`, `lgn_v_*`), nombres
físicos de estilo A: clave `pk_`, texto `des_`, importe `imp_`, resto `val_`, fechas `dt_`.

## fld_landinglgn (tal cual)

`gmaps_run` (cada llamada al actor con su entrada completa y coste), `gmaps_place` (cada ficha, JSON
íntegro), `customer_extract_file` / `_row`, `cockpit_export_file` / `_row`, `import_list_file` / `_row`,
`web_crawl`, `web_page` (HTML comprimido), `llm_request` (la petición exacta), `llm_response`,
`reference_geo` (límites oficiales en GeoJSON), `landing_meta`.

## fld_opslgn (control)

`pipeline_run` (una fila por cambio de estado), `config_snapshot`, `search_query_log`, `llm_batch`
(PREPARING antes de llamar al proveedor), `cost_ledger` (estimado y real; el real suma solo las llamadas pagadas de la ejecución, reintentos
incluidos, con el descuento de lote y los precios de lectura y escritura de caché, y no cuenta las respuestas reutilizadas), `suppression_list`,
`seed_load`, `export_log`, `capture_batch` y `capture_batch_lead` (una empresa puede estar en varias tandas).

## fld_bizlgn

| Grupo | Tablas |
|---|---|
| Configuración (Intelek) | 23 `biz_cfg_*` y sus vistas `biz_cfg_*_current` ([Configuración](configuracion.md)) |
| Escritas por Intelek | `biz_lead_validation`, `biz_run_approval`, `biz_suppression_request`, `biz_run_request` |
| Resultados del motor | `place_norm_out`, `business_link_out`, `business_alias_out`, `merge_suspect_out`, `customer_norm_out`, `rule_evaluation_out`, `category_seen_out`, `customer_match_out`, `web_validation_out`, `score_component_out`, `score_out`, `review_reference_out`, `exclusion_out`, `export_lead_out`, `cockpit_outcome_out` |
| Hechos vigentes (dbt) | `lgn_f_rule_evaluation`, `lgn_f_customer_match`, `lgn_f3_score_component`, `lgn_f_score`, `lgn_f_exclusion`, `lgn_f_export_lead`, `lgn_f_validation` |
| Consumo (dbt) | `lgn_v_lead`, `lgn_v_lead_discarded`, `lgn_v_validation_queue`, `lgn_v_category_pending`, `lgn_v_run_summary`, `lgn_v_salesforce_export`, `lgn_v_kpi_validation`, `lgn_v_map_zones`, `lgn_v_map_leads`, `lgn_v_published_customer_conflict`, `lgn_v_coverage` |

## Identidad

- `place_id`: una ficha de Google.
- `business_id`: una empresa, `cityHash64(place_id)` de su primera ficha, fijo para siempre. Una ficha
  nueva con un teléfono ya registrado hereda la empresa si el nombre se parece (≥ 30) o está a 500 m o
  menos; si no, quedan separadas y marcadas `is_merge_suspect`. Si una ficha une dos empresas, sobrevive
  la más antigua y la otra queda como alias.
- `lead_code`: `<país>-<business_id>`, el que viaja a Salesforce y vuelve en el CSV del cockpit.
- Grano de un lead: empresa × perfil (la misma empresa con dos perfiles se analiza y puntúa dos veces).
