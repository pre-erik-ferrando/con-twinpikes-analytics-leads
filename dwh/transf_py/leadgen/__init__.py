"""Lead Generator DACH engine: the Python steps of the lead pipeline (capture, normalization,
identity, filtering, customer matching, web crawl, LLM analysis, scoring, export).

Pure modules (no I/O, unit-tested): domain, normalize, geo, identity, config, filtering, matching,
content, prompt, response, scoring, export, runs, cost.
I/O modules: db (ClickHouse), store (the read/write seam), apify, crawl, llm, loaders, seed_loader,
steps (one module per run type).
"""
