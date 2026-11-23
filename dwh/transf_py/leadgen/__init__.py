"""Lead Generator DACH engine: the Python steps of the lead pipeline (capture, normalization,
identity, customer matching, web crawl, LLM analysis, export). The relevance filter and the score are
computed in SQL (dbt views lgn_f_rule_evaluation and lgn_f2_score) and read through the store
(filter_decisions, lead_scores).

Pure modules (no I/O, unit-tested): domain, normalize, geo, identity, config, matching,
content, prompt, response, export, runs, cost.
I/O modules: db (ClickHouse), store (the read/write seam), apify, crawl, llm, loaders, seed_loader,
steps (one module per run type).
"""
