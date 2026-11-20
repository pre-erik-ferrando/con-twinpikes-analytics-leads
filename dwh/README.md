# dwh/ — the Lead Generator's data warehouse

Everything database-side (ClickHouse). The entry point is [`leadgen.py`](../leadgen.py).

| Folder | Purpose |
|---------|----------|
| [`transf_py/`](transf_py) | The engine, in Python because it calls Apify, crawls websites and calls the model, and because normalization, identity and fuzzy matching need Python libraries: [`transf_py/leadgen/`](transf_py/leadgen/README.md). |
| [`transf_dbt/`](transf_dbt) | The dbt project (`dbt-clickhouse`), views only: `src → stg → crp → biz`. See [`transf_dbt/README.md`](transf_dbt/README.md). |
| [`governance/`](governance) | Databases, roles and GRANTs: `schemas/<database>/` + `users/`. **Written, not executed** (see [`governance/README.md`](governance/README.md)). |

There is no `extraction/` folder (the template's CSV loader): the files this project receives (customer
extracts, Salesforce status files, the inputs Excel, boundaries) are loaded by the engine's
[`loaders.py`](transf_py/leadgen/loaders.py) and [`seed_loader.py`](transf_py/leadgen/seed_loader.py).
