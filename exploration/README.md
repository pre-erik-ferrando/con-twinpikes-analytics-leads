# exploration/

Ad-hoc scripts and SQL, not part of the pipeline. Outputs with client data (company names, contacts)
are not versioned.

| Script | What |
|---|---|
| `apify_smoke.py` | Checks our actor input against the actor's schema (free); `--run` does one capped search (cents) and saves the raw items to `tmp/demo/places.json` |
| `demo_lead.py` | Takes those items through the engine in memory (no DB): capture, crawl, model (answered by hand), score, approval, Salesforce file; saves every stage's rows in `tmp/demo/stages/` and a per-place `tmp/demo/trace.md`; stages explained in [demo_lead.md](demo_lead.md) |

Notes: [findings.md](findings.md) (what testing taught us) and [open_issues.md](open_issues.md) (who
answers what).
