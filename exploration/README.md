# exploration/

Ad-hoc scripts and SQL, not part of the pipeline. Outputs with client data (company names, contacts)
are not versioned.

| Script | What |
|---|---|
| `apify_smoke.py` | Checks our actor input against the actor's schema (free); `--run` does one capped search (cents) and saves the raw items to `tmp/demo/places.json` |
| `openai_smoke.py` | One invented lead through the same request `enrich` builds (free dry run); `--run` makes one paid call (about a cent) to the configured OpenAI model and runs enrich's checks on the answer |
| `run_trace.py` | A real run read back from ClickHouse (read-only): `run_trace.py cb_…` writes every step's rows to `traces/<cb>/` and one section per business in `trace.md` |
| `enrich_probe.py` | Enrichment of chosen businesses outside the pipeline (e.g. ones the filter dropped): same crawler, prompt, model and checks; reads ClickHouse, writes nothing; a few cents per business |
| `demo_lead.py` | Takes those items through the engine in memory (no DB): capture, crawl, model (answered by hand), score, approval, Salesforce file; saves every stage's rows in `tmp/demo/stages/` and a per-place `tmp/demo/trace.md`; `--inputs` uses TWP's inputs Excel, `--customers` Fluidra Austria's customer list, `--area` another district and `--places` several saved runs (one per term); stages explained in [demo_lead.md](demo_lead.md) |

Notes: [findings.md](findings.md) (what testing taught us) and [open_issues.md](open_issues.md) (who
answers what).
