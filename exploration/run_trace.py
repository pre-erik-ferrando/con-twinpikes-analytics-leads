"""Every intermediate step of a real run, read back from ClickHouse: given a capture batch, its capture and
enrichment runs, table by table, plus one section per business. Read-only, free.

    python exploration/run_trace.py cb_66cb495f9b90

Output in traces/<capture_batch_id>/ (gitignored: it holds leads and customer names):
    01_capture/<table>.csv   search log (with each Apify call), the items, normalised places, identity, filter (current), 1st match
    02_enrich/<table>.csv    crawls, pages (text size, no HTML), model requests and answers, 2nd match, and
                             the score (lgn_f_score: frozen at export or live; lgn_f2_score_component)
    trace.md                 one section per business: Google -> filter -> Fluidra client? (Google info)
                             -> crawling -> Fluidra client? (crawling) -> model -> score
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dwh" / "transf_py"))

from leadgen.state import business_views  # noqa: E402
from leadgen.store import ClickHouseStore  # noqa: E402

MAX_CELL = 20000


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


def cell(v):
    if isinstance(v, (list, tuple, dict)):
        v = json.dumps(v, ensure_ascii=False, default=str)
    s = "" if v is None else str(v)
    return s if len(s) <= MAX_CELL else s[:MAX_CELL] + f"… [{len(s)} chars]"


def save(folder: Path, table: str, rows: list[dict], drop=()) -> int:
    folder.mkdir(parents=True, exist_ok=True)
    cols = [c for c in (rows[0] if rows else {}) if c not in drop]
    with (folder / f"{table}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows({c: cell(r.get(c)) for c in cols} for r in rows)
    return len(rows)


def latest(rows: list[dict], key: str) -> dict:
    out = {}
    for r in sorted(rows, key=lambda r: str(r.get("inserted_at") or r.get("loaded_at") or "")):
        out[r[key]] = r
    return out


def match_text(m: dict) -> str:
    """One customer-match row in words: who it matched and by what, or why nobody."""
    cands = json.loads(m.get("candidates_json") or "[]")
    names = "; ".join(f"{c['customer_code']} {' '.join(str(c['customer_name']).split())} (name {c['similarity']})"
                      for c in cands)
    dropped = f", and {m['n_candidates_dropped']} more not sent" if int(m.get("n_candidates_dropped") or 0) else ""
    if m.get("outcome") == "CUSTOMER":
        return f"CUSTOMER by {m.get('signal')} — {' '.join(str(m.get('reason_text') or '').split())}"
    if m.get("outcome") == "CANDIDATES":
        return f"CANDIDATES, sent to the model: {names}{dropped}"
    if m.get("outcome") == "NO_MATCH" and cands:
        return f"NO_MATCH — similar names, ruled out by the model (DIFFERENT): {names}{dropped}"
    if m.get("outcome") == "NO_MATCH":
        return ("NO_MATCH — no customer of the country shares VAT, register, phone or domain, and none has a "
                "similar name")
    return f"{m.get('outcome')} — {m.get('reason_text') or ''}"


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("capture_batch_id")
    ns = a.parse_args()
    load_env()
    st = ClickHouseStore()
    cb = ns.capture_batch_id
    out = ROOT / "traces" / cb

    runs = st.select("pipeline_run", {"capture_batch_id": cb})
    cap = sorted({r["run_id"] for r in runs if r["run_type"] == "CAPTURE"})
    enr = sorted({r["run_id"] for r in runs if r["run_type"] == "ENRICH"})
    if not cap:
        sys.exit(f"no capture run for {cb}")
    print(f"{cb}: capture {cap}, enrich {enr}")
    s1, s2 = out / "01_capture", out / "02_enrich"
    n = {}
    n["pipeline_run"] = save(out, "pipeline_run", sorted(runs, key=lambda r: str(r["inserted_at"])))
    n["search_query_log"] = save(s1, "search_query_log", st.select("search_query_log", {"capture_batch_id": cb}))
    n["gmaps_place"] = save(s1, "gmaps_place", st.select("gmaps_place", {"run_id": cap}))
    places = st.select("place_norm_out", {"run_id": cap})
    n["place_norm_out"] = save(s1, "place_norm_out", places)
    links = st.select("business_link_out", {"run_id": cap})
    n["business_link_out"] = save(s1, "business_link_out", links)
    batch = st.select("capture_batch", {"capture_batch_id": cb})[0]
    bids = {r["business_id"] for r in st.select("capture_batch_lead", {"capture_batch_id": cb})}
    # the filter is computed in SQL with the current configuration (lgn_f_rule_evaluation)
    rules = list(st.filter_decisions(batch["country"], batch["profile"], bids).values())
    n["lgn_f_rule_evaluation"] = save(s1, "lgn_f_rule_evaluation", rules)
    m1 = st.select("customer_match_out", {"run_id": cap})
    n["customer_match_out (capture)"] = save(s1, "customer_match_out", m1)
    n["capture_batch_lead"] = save(s1, "capture_batch_lead", st.select("capture_batch_lead", {"capture_batch_id": cb}))

    crawls, reqs, resps, valid, m2, scores, comps = [], [], [], [], [], [], []
    if enr:
        crawls = st.select("web_crawl", {"run_id": enr})
        n["web_crawl"] = save(s2, "web_crawl", crawls)
        pages = st.select("web_page", {"crawl_id": [c["crawl_id"] for c in crawls]},
                          columns=["crawl_id", "url", "depth", "status_code", "content_type", "text_chars",
                                   "content_hash", "fetched_at"]) if crawls else []
        n["web_page"] = save(s2, "web_page", pages)
        reqs = st.select("llm_request", {"run_id": enr})
        n["llm_request"] = save(s2, "llm_request", reqs)
        resps = st.select("llm_response", {"custom_id": [r["custom_id"] for r in reqs]}) if reqs else []
        n["llm_response"] = save(s2, "llm_response", resps)
        valid = st.select("web_validation_out", {"run_id": enr})
        n["web_validation_out"] = save(s2, "web_validation_out", valid)
        m2 = st.select("customer_match_out", {"run_id": enr})
        n["customer_match_out (enrich)"] = save(s2, "customer_match_out", m2)
        # the score is computed in SQL: the one shown (frozen at export, else live) and the live buckets
        bids = [r["business_id"] for r in st.select("capture_batch_lead", {"capture_batch_id": cb})]
        q = st.client.query
        cols = ["business_id", "country", "profile", "total", "rating", "breakdown", "score_source", "export_id"]
        scores = [dict(zip(cols, r)) for r in q(f"SELECT {', '.join(cols)} FROM fld_bizlgn.lgn_f_score "
                                                "WHERE business_id IN %(b)s", parameters={"b": bids}).result_rows]
        n["lgn_f_score"] = save(s2, "lgn_f_score", scores)
        ccols = ["business_id", "factor", "bucket_code", "points", "input_value"]
        comps = [dict(zip(ccols, r)) for r in q(f"SELECT {', '.join(ccols)} FROM fld_bizlgn.lgn_f2_score_component "
                                                "WHERE business_id IN %(b)s", parameters={"b": bids}).result_rows]
        n["lgn_f2_score_component"] = save(s2, "lgn_f2_score_component", comps)
    n["cost_ledger"] = save(out, "cost_ledger", st.select("cost_ledger", {"run_id": cap + enr}))
    for k, v in n.items():
        print(f"  {k}: {v} rows")

    # one section per business
    place_of = latest(places, "place_id")
    bid_places: dict[int, list[str]] = {}
    for l in links:
        bid_places.setdefault(l["business_id"], []).append(l["place_id"])
    rule = latest(rules, "business_id")
    match1, match2 = latest(m1, "business_id"), latest(m2, "business_id")
    val, score = latest(valid, "business_id"), latest(scores, "business_id")
    crawl_of = {}                     # domain -> its latest crawl in this enrichment
    for c in sorted(crawls, key=lambda c: str(c.get("loaded_at") or "")):
        crawl_of[c["domain"]] = c
    lines = [f"# Run trace {cb}", "", f"Capture {', '.join(cap)}; enrichment {', '.join(enr) or 'none'}.", ""]

    def outcome(bid):
        if rule.get(bid, {}).get("decision") == "EXCLUDE":
            return "excluded by the filter"
        if match1.get(bid, {}).get("outcome") == "CUSTOMER":
            return "Fluidra client (Google info)"
        if match2.get(bid, {}).get("outcome") == "CUSTOMER":
            return "Fluidra client (crawling)"
        if bid in score:
            return f"LEAD {score[bid]['rating']} ({score[bid]['total']})"
        return "not scored"

    # the business as the filter and the score see it: master listing, every listing's categories, most reviews
    views = business_views(st, batch["country"], set(bid_places))
    order = sorted(bid_places, key=lambda b: (not outcome(b).startswith("LEAD"), outcome(b)))
    for bid in order:
        v = views.get(bid)
        p = v.master if v else place_of.get(bid_places[bid][0], {})
        n = len(v.place_ids) if v else 1
        cats = ", ".join(v.categories) if v else p.get("main_category")
        reviews = (v.reviews_count if v else p.get("reviews_count")) or 0
        lines += [f"## {p.get('name')} — {outcome(bid)}", "",
                  f"1. **Google**{f' ({n} listings, master shown)' if n > 1 else ''}: {p.get('address')} | {cats} | "
                  f"web {p.get('website') or '-'} | tel {p.get('phone_e164') or '-'} | "
                  f"district {p.get('subregion_code') or '-'} | {reviews} reviews"]
        r = rule.get(bid, {})
        lines.append(f"2. **Filter** (current configuration): {r.get('decision')} ({r.get('stage') or '-'}, "
                     f"{r.get('rule')}, `{r.get('trigger_value') or ''}`)")
        m = match1.get(bid)
        if m:
            lines.append(f"3. **Fluidra client? from Google info**: {match_text(m)}")
        w = val.get(bid)
        if w:
            c = crawl_of.get(w.get("domain") or "", {})
            site = w.get("domain") or "-"
            if w.get("final_domain") and w.get("final_domain") != w.get("domain"):
                site += f" → {w['final_domain']}"
            found = [f"legal name {w['legal_name']}" if w.get("legal_name") else None,
                     f"address {w['legal_address']}" if w.get("legal_address") else None,
                     f"VAT {w['vat_id']} ({w.get('vat_check_status') or 'not checked'})" if w.get("vat_id") else None,
                     f"register {w['register_id']}" if w.get("register_id") else None,
                     f"contact {w['contact_name']}" + (f" ({w['contact_role']})" if w.get("contact_role") else "")
                     if w.get("contact_name") else None,
                     f"email {w['email']}" if w.get("email") else None,
                     f"phone {w['phone_e164']}" if w.get("phone_e164") else None,
                     "online shop" if w.get("has_ecommerce") else None]
            pages = f", {c['n_pages']} pages ({c.get('render_method') or '-'})" if c else ""
            lines.append(f"4. **Crawling**: {site}: {w.get('crawl_status')}{pages} | the website gives: "
                         f"{'; '.join(x for x in found if x) or 'nothing usable'}")
        m = match2.get(bid)
        if m:
            lines.append(f"5. **Fluidra client? from crawling**: {match_text(m)}")
        if w:
            lines.append(f"6. **Model**: {w.get('validation')} — {w.get('validation_reason_text') or ''} | type "
                         f"{w.get('company_type')} | brands {w.get('detected_brands')} | Fluidra "
                         f"{w.get('fluidra_brands')} | dropped claims {w.get('n_unsupported_claims')}")
        s = score.get(bid)
        if s:
            lines.append(f"7. **Score**: {s.get('total')} → {s.get('rating')} (type, brands, zone, digital, contact, "
                         f"reviews = {s.get('breakdown')})")
        lines.append("")
    (out / "trace.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{len(order)} businesses -> {(out / 'trace.md').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
