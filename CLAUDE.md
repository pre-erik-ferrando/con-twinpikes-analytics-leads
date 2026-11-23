# con-twinpikes-analytics-leads — CLAUDE.md

Lead Generator DACH for Fluidra (client "twinpikes", with TWP): Google Maps capture -> filter ->
customer match -> website + model -> score -> Sales Ops validation -> Salesforce export, on ClickHouse,
read by Intelek's DataApp. Overview and environment: [README.md](README.md). Spec: [docs/spec/](docs/spec)
(definition v3.3 + the technical review of v3.2). Where the code departs from it: [decisiones.md](docs/outline/decisiones.md).

## Flow

```
leadgen.py ──▶ dwh/transf_py/leadgen (engine, Python) ──▶ fld_landinglgn / fld_opslgn / fld_bizlgn.*_out
                                                       ──▶ dbt views src → stg → crp → biz (lgn_v_*) ──▶ Intelek
Intelek writes biz_cfg_* and biz_lead_validation; the engine reads them (config at the run's snapshot).
```

| Where | What |
|---|---|
| `leadgen.py` | the only entry point (runs, loaders, validation helpers, `dbt`) |
| `dwh/transf_py/leadgen/` | the engine; `ddl.py` is the contract of EVERY table it writes |
| `dwh/transf_py/leadgen/seeds/` | our configuration values (CSV, logical names) |
| `dwh/transf_dbt/` | dbt views; `models/leads/_sources.yml` is GENERATED from `ddl.py` |
| `dwh/governance/` | hand-run DDL and grants (a request, not run from here) |
| `docs/outline/` | Spanish docs synced one way to Outline (`sync.py`) |

## Commands

```bash
source .venv/bin/activate
./run_tests.sh                  # no-DB suite (CI runs the same)
./run_db_tests.sh               # engine + dbt on a throwaway local ClickHouse (CI job `db`)
cd dwh/transf_py && python -m leadgen.ddl > ../transf_dbt/models/leads/_sources.yml   # after any ddl.py change
```

Runs that call Apify or the model cost money and write to the shared DB: the user launches them; don't.

## Rules

- Code, comments, READMEs: English. `docs/outline/`: Spanish. Commit messages: Spanish, `tipo(ámbito): resumen`.
- Never commit client data (customer extracts, Salesforce status files, the inputs Excel, Salesforce files, leads).
- Insert-only everywhere: no UPDATE, no DELETE, no FINAL; the current row is argMax by (inserted_at, updated_by).
  A config "delete" is a new row with active = 0.
- Never invent a parameter: business values are biz_cfg_* rows (seeds or TWP's Excel), marked [PROPUESTA]
  when ours. A new table or column starts in `ddl.py`, then regenerate `_sources.yml`.
- Logical names in code; style A physical names (pk_/des_/imp_/val_) only in tables Intelek reads or writes,
  translated by `store.py`.
- The DACH Pool-specialist prompt is the definition's text (unchanged from v3.2 to v3.3): changing it changes `prompt_version` (cache, custom_id).
- When code or a decision changes, update the matching `docs/outline/` page in the same change.
