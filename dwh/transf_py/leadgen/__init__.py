"""Lead Generator DACH engine: the Python steps of the lead pipeline (capture, normalization,
identity, filtering, customer matching, web crawl, LLM analysis, export). The score is computed in SQL
(dbt view lgn_f2_score) and read with store.lead_scores.

Pure modules (no I/O, unit-tested): domain, normalize, geo, identity, config, filtering, matching,
content, prompt, response, export, runs, cost.
I/O modules: db (ClickHouse), store (the read/write seam), apify, crawl, llm, loaders, seed_loader,
steps (one module per run type).
"""
