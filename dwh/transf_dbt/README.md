# transf_dbt/ — dbt views of the Lead Generator

Project `leadgen_dwh_ch`, profile `leadgen_dwh_creds` ([profiles.yml](profiles.yml), `env_var()` only).
Every model is a **view**: the engine writes insert-only tables all the time and Intelek must see a new
run or a decision without a rebuild. dbt never creates a table: the engine's tables are created by
`python leadgen.py init` and are **sources** here ([`models/leads/_sources.yml`](models/leads/_sources.yml),
generated from [`ddl.py`](../transf_py/leadgen/ddl.py)).

```
models/leads/
├── srclgn/   lgn_e_*  typed landing (actor items, categories, pages, boundaries)
├── stglgn/   lgn_a_*  current configuration (argMax per key, active only)
├── crplgn/   lgn_d_*  place, business (master listing), alias, customer, extract, enrichment, Salesforce status
└── bizlgn/   lgn_f_*  current decisions (filter, customer match, score, exclusion, export, validation)
              lgn_v_*  what Intelek reads, style A names: lead, discarded, validation queue, pending
                       categories, run summary, Salesforce export preview, KPIs, maps, conflicts, coverage
```

| Intelek screen (DF.13) | View |
|---|---|
| Capture: map 1 | `lgn_v_map_zones` |
| Enrichment: leads and exclusions | `lgn_v_lead`, `lgn_v_lead_discarded`, `lgn_v_category_pending` |
| Validation: indicators, map 2, table | `lgn_v_kpi_validation`, `lgn_v_map_leads`, `lgn_v_validation_queue` |
| Export: table, map 3 | `lgn_v_salesforce_export`, `lgn_v_lead`, `lgn_v_map_zones`, `lgn_v_map_zones_month` (map 3 by Salesforce status month) |
| Operation | `lgn_v_run_summary`, `lgn_v_published_customer_conflict`, `lgn_v_coverage` |

## Commands

```bash
python leadgen.py dbt                      # dbt build (loads .env, sets DBT_CH_SCHEMA=fld_landinglgn)
python leadgen.py dbt -- test              # the singular guards (tests/biz/assert_*.sql)
pytest                                     # this folder's no-DB tests
```

## SQL traps (ClickHouse)

- `LEFT JOIN` without a match gives `''` or `NULL` depending on `join_use_nulls`: every view with a
  LEFT JOIN sets `SETTINGS join_use_nulls = 1`, so a non-match is NULL (`isNull`, `coalesce`).
- An alias equal to a column inside an aggregate (`argMax(x, …) AS x`) is a cyclic alias: qualify the
  column (`src.x`).
- `LowCardinality(Nullable(String))`, never `Nullable(LowCardinality(String))`.
- No `FINAL`: the current row is `argMax` / `row_number()` by `inserted_at`.
