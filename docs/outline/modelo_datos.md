# Modelo de datos

El contrato de cada tabla que escriben el motor o Intelek está en un solo sitio,
[`ddl.py`](../../dwh/transf_py/leadgen/ddl.py); las fuentes de dbt se generan de él. Nombres lógicos en el
código; en las tablas que lee o escribe Intelek (`biz_cfg_*`, `biz_lead_validation`, `lgn_v_*`), nombres
físicos de estilo A: clave `pk_`, texto `des_`, importe `imp_`, resto `val_`, fechas `dt_`.

## fld_landinglgn (tal cual)

`gmaps_place` (cada ficha, JSON íntegro), `upload_file` / `upload_row` (cada fichero subido y sus filas
tal cual: extracto de clientes, fichero de estados de Salesforce o listado propio, según `kind`),
`web_crawl`, `web_page` (HTML comprimido), `llm_request` (la petición exacta), `llm_response`,
`reference_geo` (límites oficiales en GeoJSON).

## fld_opslgn (control)

`pipeline_run` (una fila por cambio de estado), `config_snapshot`, `search_query_log` (cada llamada al actor: la consulta, la entrada completa enviada, su
coste y lo que devolvió), `llm_batch`
(PREPARING antes de llamar al proveedor; el lote lleva este id como etiqueta, para encontrarlo si se pierde la respuesta del envío), `cost_ledger` (estimado y real; el real suma solo las llamadas pagadas de la ejecución, reintentos
incluidos, con el descuento de lote y los precios de lectura y escritura de caché, y no cuenta las respuestas reutilizadas),
`seed_load`, `export_log`, `capture_batch` y `capture_batch_lead` (una empresa puede estar en varias tandas).

## fld_bizlgn

| Grupo | Tablas |
|---|---|
| Configuración (Intelek) | 19 `biz_cfg_*` y sus vistas `biz_cfg_*_current` ([Configuración](configuracion.md)) |
| Escritas por Intelek | `biz_lead_validation`, `biz_run_approval`, `biz_suppression_request`, `biz_run_request` |
| Resultados del motor | `place_norm_out`, `business_link_out`, `business_identity_out` (empresas absorbidas y posibles duplicados), `customer_norm_out`, `customer_match_out`, `web_validation_out`, `export_lead_out` (con la puntuación congelada al exportar). Lo que es una regla sobre otras tablas no se guarda: las exclusiones (`lgn_f_exclusion`), los estados de Salesforce (`lgn_d_sf_status_row` con cada fila del fichero y sus avisos, `lgn_d_sf_status` por mes) y las categorías sin clasificar (`lgn_v_category_pending`) son vistas, y el motor las lee (`store.read_view`) |
| Hechos vigentes (dbt) | `lgn_f_rule_evaluation` (el filtro de relevancia: la única implementación, en SQL, con la configuración vigente, sobre los textos que el motor normaliza en `place_norm_out`), `lgn_f_customer_match`, `lgn_f_score` (la congelada al exportar, si no la viva), `lgn_f_exclusion`, `lgn_f_export_lead`, `lgn_f_validation` |
| Puntuación (dbt) | `lgn_f2_score_component` (un tramo por factor) y `lgn_f2_score` (total, rating, desglose): la única implementación de la puntuación, en SQL y con la configuración vigente (`lgn_a_scoring_rule`, `lgn_a_rating_band`, `lgn_a_brand`, `lgn_a_shared_domain`, `lgn_a_run_parameter`). El motor la lee (`store.lead_scores`) y la congela al exportar. `run_db_tests.sh` la comprueba contra lo que daba el motor en Python y contra los ejemplos de la definición |
| Consumo (dbt) | `lgn_v_lead`, `lgn_v_lead_discarded`, `lgn_v_validation_queue`, `lgn_v_category_pending`, `lgn_v_run_summary`, `lgn_v_salesforce_export`, `lgn_v_kpi_validation`, `lgn_v_map_zones`, `lgn_v_map_zones_month`, `lgn_v_map_leads`, `lgn_v_published_customer_conflict`, `lgn_v_coverage` |

## Identidad

- `place_id`: una ficha de Google.
- `business_id`: una empresa, `cityHash64(place_id)` de su primera ficha, fijo para siempre. Una ficha
  nueva con un teléfono ya registrado hereda la empresa si el nombre se parece (≥ 30) o está a 500 m o
  menos; si no, quedan separadas y marcadas `is_merge_suspect`. Si una ficha une dos empresas, sobrevive
  la más antigua y la otra queda como alias. Todo lo de la absorbida (validaciones, exportaciones, análisis
  de la web, cruce con clientes, estados de Salesforce, puntuación congelada) pasa a la que sobrevive, también
  en cadena (A → B → C, hasta cuatro saltos en SQL): un lead exportado antes de la fusión no se exporta otra
  vez, y aparece una sola vez en `lgn_v_lead`.
- `lead_code`: `<país>-<business_id>`, el que viaja a Salesforce y vuelve en el fichero de estados de Salesforce.
- Grano de un lead: empresa × perfil (la misma empresa con dos perfiles se analiza y puntúa dos veces).
