"""Every intermediate step of a real run, read back from ClickHouse: given a capture batch, its capture and
enrichment runs, table by table, plus one section per business. Read-only, free.

    python exploration/run_trace.py cb_66cb495f9b90

Output in tmp/trace/<capture_batch_id>/ (gitignored: it holds leads and customer names):
    01_capture/<table>.csv   search log, Apify calls and items, normalised places, identity, filter, 1st match
    02_enrich/<table>.csv    crawls, pages (text size, no HTML), model requests and answers, 2nd match, and
                             the score (lgn_f_score: frozen at export or live; lgn_f2_score_component)
    trace.md                 one section per business: Google -> filter -> customer match -> website/model -> score
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


def main() -> int:
    a = argparse.ArgumentParser()
    a.add_argument("capture_batch_id")
    ns = a.parse_args()
    load_env()
    st = ClickHouseStore()
    cb = ns.capture_batch_id
    out = ROOT / "tmp" / "trace" / cb

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
    n["gmaps_run"] = save(s1, "gmaps_run", st.select("gmaps_run", {"run_id": cap}))
    n["gmaps_place"] = save(s1, "gmaps_place", st.select("gmaps_place", {"run_id": cap}))
    places = st.select("place_norm_out", {"run_id": cap})
    n["place_norm_out"] = save(s1, "place_norm_out", places)
    links = st.select("business_link_out", {"run_id": cap})
    n["business_link_out"] = save(s1, "business_link_out", links)
    rules = st.select("rule_evaluation_out", {"run_id": cap})
    n["rule_evaluation_out"] = save(s1, "rule_evaluation_out", rules)
    n["category_seen_out"] = save(s1, "category_seen_out", st.select("category_seen_out", {"run_id": cap}))
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
    lines = [f"# Run trace {cb}", "", f"Capture {', '.join(cap)}; enrichment {', '.join(enr) or 'none'}.", ""]

    def outcome(bid):
        if rule.get(bid, {}).get("decision") == "EXCLUDE":
            return "excluded by the filter"
        if match1.get(bid, {}).get("outcome") == "CUSTOMER":
            return "customer (capture)"
        if match2.get(bid, {}).get("outcome") == "CUSTOMER":
            return "customer (website)"
        if bid in score:
            return f"LEAD {score[bid]['rating']} ({score[bid]['total']})"
        return "not scored"

    order = sorted(bid_places, key=lambda b: (not outcome(b).startswith("LEAD"), outcome(b)))
    for bid in order:
        p = place_of.get(bid_places[bid][0], {})
        lines += [f"## {p.get('name')} — {outcome(bid)}", "",
                  f"1. **Google**: {p.get('address')} | {p.get('main_category')} | web {p.get('website') or '-'} | "
                  f"tel {p.get('phone_e164') or '-'} | district {p.get('subregion_code') or '-'} | "
                  f"{p.get('reviews_count') or 0} reviews"]
        r = rule.get(bid, {})
        lines.append(f"2. **Filter**: {r.get('decision')} ({r.get('stage') or '-'}, {r.get('rule')}, `{r.get('trigger_value') or ''}`)")
        for label, m in (("3. **Match, capture**", match1.get(bid)), ("4. **Match, website**", match2.get(bid))):
            if m:
                lines.append(f"{label}: {m.get('outcome')} {m.get('signal') or ''} {m.get('customer_code') or ''} "
                             f"{m.get('customer_name') or ''} — {m.get('reason_text') or ''}")
        v = val.get(bid)
        if v:
            lines.append(f"5. **Model**: {v.get('validation')} — {v.get('validation_reason_text') or ''} | type "
                         f"{v.get('company_type')} | legal {v.get('legal_name') or '-'} | VAT {v.get('vat_id') or '-'} | "
                         f"contact {v.get('contact_name') or '-'} {v.get('email') or ''} | brands "
                         f"{v.get('detected_brands')} | Fluidra {v.get('fluidra_brands')} | dropped claims "
                         f"{v.get('n_unsupported_claims')}")
        s = score.get(bid)
        if s:
            lines.append(f"6. **Score**: {s.get('total')} → {s.get('rating')} (type, brands, zone, digital, contact, "
                         f"reviews = {s.get('breakdown')})")
        lines.append("")
    (out / "trace.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n{len(order)} businesses -> {(out / 'trace.md').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
