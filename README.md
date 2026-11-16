# con-twinpikes-analytics-leads

**Lead Generator DACH** for Fluidra (TwinPikes project, with TWP): finds pool businesses in Germany,
Austria and Switzerland (Belgium and the Netherlands prepared) that are not yet Fluidra customers, scores
them with TWP's profile model and delivers the approved ones to Salesforce, scored, before Inside Sales
calls them. It replaces the n8n flow "Lead Discovery Engine", its Google sheet and the prioritisation
Excel.

Built on **ClickHouse**: a Python engine (capture, normalization, identity, filter, customer match,
website crawl, model analysis, scoring, export) plus **dbt** views that Intelek's DataApp reads. Same
stack and conventions as [`con-twinpikes-analytics`](https://github.com/Prenomics/con-twinpikes-analytics)
(the Insight Generator), whose skeleton this repo copies.

The specification is in [`docs/spec/`](docs/spec): the functional and technical definition v3.2 and the
technical review of v3.2. What this delivery builds and what it leaves out: [Proyecto Lead
Generator](docs/outline/proyecto_lead_generator.md); where the code departs from the Word, and why:
[Decisiones](docs/outline/decisiones.md). How it all works, end to end: [How it works](docs/how_it_works.md).

> ⚠️ Everything is launched with [`leadgen.py`](leadgen.py) (its header lists every command). Until
> Intelek can launch runs and upload files (DT.2.7), that is the DataApp's back end.

---

## The weekly flow (DF.4)

```
capture  ──▶ enrich ──▶ validation ──▶ export ──▶ (call in the cockpit / Salesforce) ──▶ call results
Google Maps   website     Sales Ops      xlsx for                                        load-cockpit
filter        model       rejects with   Salesforce
1st match     2nd match   a reason
              score
                                          monthly: load-customers ──▶ rematch
```

```bash
python leadgen.py capture --country AT --areas AT-501 --terms Poolbau,Poolshop   # -> capture batch cb_…
python leadgen.py enrich --capture-batch cb_…                                    # cost approval if above 10 USD
python leadgen.py review --capture-batch cb_…                                    # or Intelek, lgn_v_validation_queue
python leadgen.py approve-rest --capture-batch cb_… --yes
python leadgen.py export --capture-batch cb_… --out exports/
python leadgen.py confirm-export exp_… --status UPLOADED
```

Step by step, with the monthly tasks: [Operación](docs/outline/operacion.md).

---

## Reproducing the environment

Python **3.12** (CI pins it in [`.github/workflows/tests.yml`](.github/workflows/tests.yml)).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r dwh/transf_py/leadgen/requirements.txt -r dwh/transf_dbt/requirements.txt \
            -r dwh/transf_dbt/dev-requirements.txt
cp .env.example .env            # and fill it in: ClickHouse, Apify, Anthropic (ask for the values)
```

- Databases need the `fld_` prefix and its GRANTs, requested before the first run:
  [`dwh/governance/README.md`](dwh/governance/README.md).
- ClickHouse is behind the VPN. `leadgen.py` loads `.env` itself; plain `dbt` does not
  (`python leadgen.py dbt` does).

First start, once: `init` (tables), `seed` (our values), `load-config` (TWP's inputs Excel),
`load-geo` (boundaries), `load-customers` (one extract per country), `dbt` (views). Detail:
[Onboarding](docs/outline/onboarding.md).

### Check it works

```bash
./run_tests.sh                       # every check that needs no ClickHouse, no network, no credentials
python leadgen.py precheck --country AT --areas AT-501 --terms Poolbau    # the configuration, against the DB
```

The test suite runs every run type end to end on an in-memory store with a fake Apify, fake websites
and a fake model, so a green `./run_tests.sh` means the flow works; it does not prove the ClickHouse SQL,
which only `python leadgen.py dbt -- test` does.

---

## What is in here

```
con-twinpikes-analytics-leads/
├── leadgen.py               THE ENTRY POINT: every run type, loader and validation helper
├── run_tests.sh             every check that needs no ClickHouse
├── pytest.ini               makes `pytest` from the root collect every suite
├── .env.example             the variables .env must define (.env is gitignored)
├── .github/                 CI: pre-commit + run_tests.sh on every push and pull request
├── .pre-commit-config.yaml  whitespace / EOF / JSON-YAML-TOML checks + large-file guard
├── dwh/                     engine (transf_py/leadgen), dbt views (transf_dbt), governance
├── docs/                    the documentation (outline/, Spanish) and the client's spec (spec/)
├── data/                    the client's files, gitignored
└── exploration/             ad-hoc scripts and queries
```

Each folder's README says what is inside: [`dwh/`](dwh/README.md) · [`docs/`](docs/README.md) ·
[`data/`](data/README.md) · [`exploration/`](exploration/README.md).
