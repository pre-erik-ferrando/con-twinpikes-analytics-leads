# leadgen/ — the Lead Generator engine

Run through [`leadgen.py`](../../../leadgen.py). Spec references (DF.x, DT.x) point to the definition v3.3
and its technical review in [`docs/spec/`](../../../docs/spec).

## Modules

| Module | Pure? | What |
|---|---|---|
| `domain.py` | ✓ | closed domains (countries, profiles, buckets, states…) |
| `ddl.py` | ✓ | the contract of every table the engine or Intelek writes; DDL, `*_current` views, dbt sources |
| `config.py` | ✓ | current config row per key (argMax, snapshot, country/profile override), typed access, precheck |
| `normalize.py` | ✓ | phone E.164, domain, name keys (umlauts both ways, legal forms), postal code, VAT, register |
| `geo.py` | ✓ | subregion/municipality by point-in-polygon (never from the postal code) |
| `identity.py` | ✓ | business_id assignment, merge by phone, aliases, master listing |
| `matching.py` | ✓ | customer match: hard signals, candidates, confirmation, verdicts, priority |
| `content.py` | ✓ | website preparation: signals, text, dedupe, budget, brand mentions |
| `prompt.py` | ✓ | system message (common + country + profile blocks), user message, JSON schema, versions |
| `response.py` | ✓ | checking the model's answer without the model (each claim on its cited page) |
| `cost.py`, `runs.py`, `export.py` | ✓ | estimate/approval, states/area locking, Salesforce rows |
| `store.py` | I/O | the seam: `ClickHouseStore` and `MemoryStore` (tests), logical ↔ physical names |
| `state.py` | I/O | current state of the insert-only tables; run context and snapshot; syncs Intelek's decisions |
| `apify.py`, `crawl.py`, `llm.py` | I/O | Google Maps actor, crawler (+ Playwright), the model: OpenAI or Anthropic, direct and batch |
| `vat.py` | I/O | VAT check (DT.5.6): VIES for EU numbers, the Swiss UID register for CHE; only fills `vat_check_status` |
| `loaders.py`, `seed_loader.py` | I/O | customer extracts, Salesforce status file, boundaries, own lists; seeds and TWP's Excel |
| `geo_convert.py` | I/O | DE/CH boundaries (GeoPackage, UTM 32N / LV95) to GeoJSON in lon/lat, for `load-geo` |
| `steps/` | I/O | one module per run type: `capture`, `enrich`, `export`, `maintenance` |

## Run types (review DT.9)

| Type | Command | Writes |
|---|---|---|
| CAPTURE | `capture` (`--retry RUN` reuses a failed run's paid Apify calls) | gmaps_place, search_query_log, place_norm_out (with the filter's normalized texts), business_link_out, capture_batch(_lead), then reads the filter (lgn_f_rule_evaluation), customer_match_out (CAPTURE) |
| IMPORT_LIST | `import-list` | as CAPTURE, source IMPORT |
| ENRICH | `enrich`, `resume` | web_crawl/web_page, llm_request/response, llm_batch, web_validation_out, customer_match_out (ENRICH); the score is the dbt view lgn_f2_score |
| EXPORT | `export`, `confirm-export` | export_lead_out (with the score frozen), export_log, the xlsx |
| REPROCESS_RULES | `reprocess` | customer_match_out of what the filter includes now (no Apify, crawl or model; the filter and the scores are live in SQL) |
| CUSTOMER_REMATCH | `rematch` | customer_match_out (REMATCH) |

Every run writes one `ops.pipeline_run` row per state change: REQUESTED → SNAPSHOT_FROZEN →
[AWAITING_COST_APPROVAL] → RUNNING → [AWAITING_BATCHES ⇄ COLLECTING] → CLOSED | FAILED | CANCELLED.
An error or Ctrl-C ends a run FAILED, never RUNNING; `cancel RUN` ends one left unfinished.

## Invariants the tests hold

- Insert-only; the current row is the latest by `(inserted_at, updated_by)`; a config row applies to its
  country/profile over the NULL row.
- A configuration snapshot is frozen at the start of a run; a resumed run keeps it.
- A model request is written (llm_request + llm_batch PREPARING) before it is sent, and the batch is
  tagged with our batch id: after a crash, `resume` finds a sent batch by its tag and sends only what
  never arrived. A cached answer (same business, prompt_version, brand list, candidates and content) is
  never paid twice. Only one unfinished ENRICH run per capture batch.
- A cost approval always has an amount.
- An approved lead whose business, listing, phone or domain is later suppressed is not exported.
- A failed, truncated or schema-invalid answer is retried once through the direct API, then the lead is
  REVIEW with reason TECHNICAL.
- Every claim of the model is dropped unless it appears on the page it cites.
- UNSURE counts as customer. Human/Salesforce corrections beat hard signals, which beat the model, which
  beats soft confirmation.
- A lead is exported once; Published only after the export is confirmed UPLOADED.

## Adding things

- **A parameter**: a row in `seeds/biz_cfg_run_parameter.csv` (or edited in Intelek); read it with
  `cfg.param(name, default)`.
- **A column or table**: `ddl.py`, then regenerate the dbt sources
  (`python -m leadgen.ddl > ../transf_dbt/models/leads/_sources.yml`), then `leadgen.py init` adds the
  table (a new column on an existing table needs an `ALTER TABLE … ADD COLUMN`, as CREATE IF NOT EXISTS
  leaves it untouched).
- **A profile**: TWP's rules as rows with `profile = <PROFILE>` (terms, categories, titles, pool signals,
  COMPANY_TYPE points), a prompt block in `prompt.PROFILE_BLOCKS`, `active = 1` in biz_cfg_profile; then
  a calibration capture and `reprocess` (review DT.4.4).
- **A country**: its areas and zones, terms in its language, brands, customer extract and source
  columns, boundaries (`load-geo`); BE and NL also need the Apify pilot (DT.3.3 of the review).
