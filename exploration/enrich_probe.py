"""Enrichment of chosen businesses, outside the pipeline: for businesses the filter set aside, would the
website and the model make them a lead? Same crawler, content preparation, prompt, model and checks as the
`enrich` step, with the configuration in force. Reads ClickHouse; writes nothing to it.

    python exploration/enrich_probe.py cb_66cb495f9b90 5084261836801569381 3207533525377963810
    python exploration/enrich_probe.py cb_… BUSINESS_ID … --dry       # crawl and build the request, no model call

Cost: one OpenAI call per business (a few cents each). Customer candidates are not sent: this checks the
profile, not whether the business is already a customer.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dwh" / "transf_py"))

from leadgen import ddl, prompt  # noqa: E402
from leadgen.config import Config  # noqa: E402
from leadgen.content import prepare  # noqa: E402
from leadgen.crawl import Crawler, CrawlLimits, playwright_renderer  # noqa: E402
from leadgen.llm import LLMRequest, make_provider  # noqa: E402
from leadgen.response import SchemaError, check_answer, check_schema  # noqa: E402
from leadgen.store import ClickHouseStore  # noqa: E402

NO_CONTENT = ("NO_WEBSITE", "SOCIAL_ONLY", "BLOCKED", "ROBOTS_DISALLOWED", "UNREACHABLE", "EMPTY")


def load_env() -> None:
    f = ROOT / ".env"
    if not f.is_file():
        return
    for line in f.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        v = "" if v.startswith("#") else v.split(" #", 1)[0].strip().strip("\"'")
        os.environ.setdefault(k.strip().removeprefix("export ").strip(), v)


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("capture_batch_id")
    a.add_argument("business_ids", nargs="+", type=int)
    a.add_argument("--dry", action="store_true", help="crawl and build the request; no model call")
    ns = a.parse_args()
    load_env()
    st = ClickHouseStore()

    runs = st.select("pipeline_run", {"capture_batch_id": ns.capture_batch_id})
    cap = sorted({r["run_id"] for r in runs if r["run_type"] == "CAPTURE"})
    country = next((r["country"] for r in runs), "AT")
    cfg = Config.from_rows({name: st.select(name) for name in ddl.CONFIG_TABLES}, country, "POOL_SPECIALIST")
    p = cfg.param
    pats = [r["pattern"] for r in sorted(cfg.rows("biz_cfg_page_priority"), key=lambda r: r.get("priority") or 0)]
    crawler = Crawler(CrawlLimits.from_config(cfg), pats, cfg.shared_domains("SITE_BUILDER"),
                      renderer=playwright_renderer)
    competitor, fluidra = cfg.brands("COMPETITOR"), cfg.brands("FLUIDRA")
    system = prompt.system_message(country, "POOL_SPECIALIST", competitor, fluidra)
    version = prompt.prompt_version(country, "POOL_SPECIALIST")
    provider = None if ns.dry else make_provider(cfg)
    max_out = int(p("llm_max_tokens_out", 2000))
    links = {r["place_id"]: r["business_id"] for r in st.select("business_link_out", {"run_id": cap})}
    places = st.select("place_norm_out", {"run_id": cap, "place_id": [k for k, v in links.items()
                                                                       if v in ns.business_ids]})
    total = 0.0
    for bid in ns.business_ids:
        m = next((r for r in places if links.get(r["place_id"]) == bid), None)
        if m is None:
            print(f"\n{bid}: not in {ns.capture_batch_id}")
            continue
        print(f"\n=== {m['name']} ({bid}) | {m.get('main_category')} | {m.get('website') or 'no website'}")
        if not m.get("website"):
            print("   no website: enrich would send it to REVIEW without the model")
            continue
        crawl = crawler.crawl(m["website"])
        print(f"   crawl {crawl.status}: {len(crawl.pages)} pages, {crawl.n_bytes:,} bytes")
        if crawl.status in NO_CONTENT or not crawl.pages:
            print("   no content: enrich would send it to REVIEW without the model")
            continue
        content = prepare(list(crawl.pages), pats, competitor + fluidra, p("token_budget_per_page", 4000),
                          p("token_budget_per_domain", 30000), p("char_cap_per_domain", 100000),
                          p("brand_snippet_reserve_ratio", 0.10), p("brand_snippets_per_brand", 3),
                          p("brand_snippet_chars", 200))
        user = prompt.user_message(
            {"name": m.get("name"), "categories": json.loads(m["categories"]) if isinstance(m.get("categories"), str)
             else (m.get("categories") or []), "owner_description": m.get("owner_description"),
             "address": m.get("address"), "country": country, "website": m.get("website"),
             "phone": m.get("phone_e164") or m.get("phone_raw")},
            [], crawl.status, content.content_line, [(pg.page_id, pg.signals) for pg in content.pages],
            [(x.brand, x.page_id, x.snippet) for x in content.mentions],
            [(pg.page_id, pg.url, pg.text) for pg in content.pages])
        print(f"   request: {len(system) + len(user):,} chars, pages {[pg.url for pg in content.pages][:6]}")
        if ns.dry:
            continue
        r = provider.send_direct([LLMRequest(prompt.custom_id(bid, version), system, user)], max_out, 1)[0]
        cost = (r.input_tokens * float(p("llm_price_in_per_mtok")) + r.output_tokens * float(p("llm_price_out_per_mtok"))
                + r.cache_read_tokens * float(p("llm_price_cache_read_per_mtok"))
                + r.cache_write_tokens * float(p("llm_price_cache_write_per_mtok"))) / 1e6
        total += cost
        print(f"   model: {r.status}, in {r.input_tokens:,} + cache read {r.cache_read_tokens:,} + cache write "
              f"{r.cache_write_tokens:,} / out {r.output_tokens:,}, ${cost:.4f}")
        raw = r.parsed()
        if raw is None:
            print("   no valid JSON:", r.error or (r.text or "")[:300])
            continue
        try:
            check_schema(raw)
        except SchemaError as e:
            print("   schema failed:", e)
            continue
        pages = {pg.page_id: (pg.url, pg.text) for pg in content.pages}
        cart = any("cart" in pg.signals for pg in content.pages)
        ch = check_answer(raw, pages, country, competitor, fluidra, [], cart)
        print(f"   VERDICT {ch.validation} ({ch.review_reason or '-'}): {ch.validation_reason_text}")
        print(f"   type {ch.company_type} | offering {ch.offering} | focus {ch.pool_focus} | legal {ch.legal_name} | "
              f"VAT {ch.vat_id} | dropped claims {ch.n_unsupported_claims}")
    if not ns.dry:
        print(f"\ntotal model cost ${total:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
