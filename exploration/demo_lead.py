"""Demo: from a real Apify result to a Salesforce lead row, with the engine running on the in-memory
store (no database), and the model's part answered by hand (e.g. in a Claude chat). Every stage's
data is saved, so you can follow how a Google Maps place becomes a lead.

    python exploration/apify_smoke.py --run           # 1) paid (cents): saves tmp/demo/places.json
    python exploration/demo_lead.py                    # 2) capture + crawl; writes the model prompts
    #    answer each tmp/demo/prompts/<id>.txt with the JSON the prompt asks for, in
    #    tmp/demo/answers/<id>.json (only the JSON object)
    python exploration/demo_lead.py                    # 3) same command: approves, exports
    python exploration/demo_lead.py --inputs FILE.xlsx # TWP's inputs Excel instead of the demo values
        [--customers FILE.xlsx --customers-date YYYY-MM-DD]   # and Fluidra Austria's customer list
        [--area 207 --places tmp/v/poolbau.json tmp/v/poolshop.json …]   # another district, one file per term

Each run starts from scratch (in memory) and replays the saved Apify items, so it is free; it only
re-downloads the websites. Output in tmp/demo/ (gitignored):
    stages/NN_<step>/<table>.csv   the rows each step added, table by table
    trace.md                       one section per place: raw -> normalized -> filter -> web
No score here: it is computed in SQL (dbt view lgn_f2_score), so only on ClickHouse (run_trace.py).
    export/                        the Salesforce upload file

DEMO-ONLY values (not business configuration, see DEMO_CONFIG): the search area, a handful of
Google category rules (the real ones come from TWP's Excel, used with --inputs) and a placeholder
sales owner (kept with --inputs: TWP's Subregion → sales rep tab is still empty). There
is no customer extract, so the customer match finds nothing and the stale-extract block is
approved for the demo.
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dwh" / "transf_py"))

from leadgen import seed_loader  # noqa: E402
from leadgen.apify import ActorRun  # noqa: E402
from leadgen.crawl import Crawler, CrawlLimits  # noqa: E402
from leadgen.llm import LLMResult  # noqa: E402
from leadgen.loaders import load_customers, load_geo  # noqa: E402
from leadgen.state import RunContext  # noqa: E402
from leadgen.steps.capture import CaptureRequest, run_capture  # noqa: E402
from leadgen.steps.enrich import EnrichRequest, run_enrich  # noqa: E402
from leadgen.steps.export import run_export  # noqa: E402
from leadgen.steps.maintenance import approve_rest  # noqa: E402
from leadgen.store import MemoryStore  # noqa: E402

OUT = ROOT / "tmp" / "demo"
AREA = "AT-601-DEMO"
BY = "demo"

DEMO_CONFIG = {
    "biz_cfg_search_area": [
        {"country": "AT", "area_id": AREA, "level": "SUBREGION", "subregion_type": "BEZIRK", "admin_code": "601",
         "area_name": "Graz(Stadt)", "region_name": "Steiermark", "default_language": "de",
         "actor_fields": json.dumps({"countryCode": "AT", "city": "Graz"})},
    ],
    # A few of the German Google categories; whatever else appears ends as DEFAULT_EXCLUDE and is
    # listed at the end, so it can be classified here and the demo re-run.
    "biz_cfg_category_rule": [
        {"country": None, "language": "de", "category": c, "classification": k} for c, k in [
            ("Schwimmbadbauer", "INCLUDE"), ("Schwimmbeckenbauer", "INCLUDE"), ("Schwimmbadzubehörgeschäft", "INCLUDE"),
            ("Schwimmbad-Zubehörhandel", "INCLUDE"), ("Poolreinigungsdienst", "INCLUDE"),
            ("Whirlpool-Händler", "INCLUDE"), ("Bauunternehmen", "CONDITIONAL"),
            ("Garten- und Landschaftsbauer", "CONDITIONAL"), ("Installateur", "CONDITIONAL"),
            ("Hotel", "EXCLUDE"), ("Baumarkt", "EXCLUDE"), ("Öffentliches Schwimmbad", "EXCLUDE"),
            ("Hallenbad", "EXCLUDE"), ("Freibad", "EXCLUDE")]
    ],
    "biz_cfg_sales_owner": [{"country": "AT", "subregion_code": None, "owner_first_name": "Demo",
                             "owner_last_name": "Owner"}],
}

# The order the stages are shown in, and which tables each one is about (the rest go under "other").
STAGES = ["01_capture", "02_enrich", "03_validation", "04_export"]


class ReplayCapturer:
    """Returns the saved Apify items instead of calling Apify: for each query, the items of its term
    (Apify's searchString), so several saved runs, one per term, replay as one capture."""
    build = "replay"

    def __init__(self, items, default_term):
        self.by_term: dict[str, list] = {}
        for it in items:
            self.by_term.setdefault(it.get("searchString") or default_term, []).append(it)

    def run(self, actor_in, max_charge_usd):
        term = (actor_in.get("searchStringsArray") or [""])[0]
        return ActorRun("replay", "SUCCEEDED", list(self.by_term.get(term, [])), 0.0, None, None)


class PendingAnswers(Exception):
    pass


class HandProvider:
    """The model, answered by hand: a request with tmp/demo/answers/<custom_id>.json gets that answer;
    the others are written to tmp/demo/prompts/ and the run stops there."""
    name, model = "HAND", "answered-by-hand"

    def _one(self, r):
        ans = OUT / "answers" / f"{r.custom_id}.json"
        if not ans.is_file():
            return None
        text = ans.read_text(encoding="utf-8").strip()
        if text.startswith("```"):
            text = text.strip("`").removeprefix("json").strip()
        return LLMResult(r.custom_id, "SUCCEEDED", text=text, stop_reason="end_turn")

    def send_direct(self, reqs, max_tokens, concurrency):
        out, missing = [self._one(r) for r in reqs], []
        (OUT / "prompts").mkdir(parents=True, exist_ok=True)
        for r, res in zip(reqs, out):
            if res is None:
                missing.append(r.custom_id)
                (OUT / "prompts" / f"{r.custom_id}.txt").write_text(
                    f"=== SYSTEM ===\n{r.system}\n\n=== USER ===\n{r.user}\n", encoding="utf-8")
        if missing:
            raise PendingAnswers(missing)
        return out

    def submit_batch(self, reqs, max_tokens):
        raise RuntimeError("the demo runs DIRECT")

    def count_tokens(self, req):
        return len(req.system + req.user) // 4


class Stages:
    """Saves the rows each step added, table by table."""

    def __init__(self, store):
        self.store, self.seen = store, {}

    def mark(self):
        self.seen = {t: len(rows) for t, rows in self.store.tables.items()}

    def dump(self, stage: str, skip=("biz_cfg_", "config_snapshot", "seed_load")):
        d = OUT / "stages" / stage
        d.mkdir(parents=True, exist_ok=True)
        n = 0
        for t, rows in sorted(self.store.tables.items()):
            new = rows[self.seen.get(t, 0):]
            if not new or t.startswith(skip):
                continue
            cols = list(dict.fromkeys(k for r in new for k in r))
            with open(d / f"{t}.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, cols)
                w.writeheader()
                w.writerows({k: _cell(r.get(k)) for k in cols} for r in new)
            n += 1
        self.mark()
        print(f"  {stage}: {n} tables saved in {d.relative_to(ROOT)}")


def _cell(v):
    if isinstance(v, (list, tuple, dict)):
        return json.dumps(v, ensure_ascii=False, default=str)
    s = "" if v is None else str(v)
    return s if len(s) <= 2000 else s[:2000] + f"… [{len(s)} chars]"


def _store(inputs: Path | None, customers: Path | None = None, customers_date: str | None = None,
           area_code: str = "601", geo_file: Path | None = None) -> MemoryStore:
    st = MemoryStore()
    seed_loader.load_csv_seeds(st, BY)
    config = DEMO_CONFIG
    if inputs:  # TWP's areas, zones, terms, rules and brands; only the placeholder owner stays ours
        seed_loader.load(st, {t: seed_loader.typed_rows(t, r) for t, r in seed_loader.excel_to_rows(inputs).items()},
                         inputs.stem, BY, inputs.name)
        st.advance(seconds=1)
        config = {"biz_cfg_sales_owner": DEMO_CONFIG["biz_cfg_sales_owner"]}
    typed = {t: seed_loader.typed_rows(t, [{k: (str(v) if v is not None else None) for k, v in r.items()}
                                           for r in rows]) for t, rows in config.items()}
    seed_loader.load(st, typed, "demo-config", BY, "demo values, not TWP's")
    # The search goes as the area's outline (load-geo in production). With --inputs and the Austrian
    # boundaries file, the area's official outline; otherwise the rectangle around Graz that was tested
    # against Apify (exploration/findings.md).
    if inputs and geo_file and geo_file.is_file():
        cfg = RunContext(st, "demo", "LOAD", "AT", "POOL_SPECIALIST", BY).config()
        twp = {a["admin_code"] for a in cfg.search_areas().values() if a.get("level") == "SUBREGION"}
        if area_code not in twp:
            raise SystemExit(f"area {area_code} is not one of TWP's Austrian districts")
        load_geo(st, geo_file, "AT", "SUBREGION", "STATISTIK_AUSTRIA", "demo", "g_id", "g_name", keep=twp)
    else:
        st.insert("reference_geo", [{"source": "DEMO", "country": "AT", "version": "demo", "admin_level": "SUBREGION",
                                     "admin_code": "601", "admin_name": "Graz(Stadt)", "geometry_json": json.dumps(
                                         {"type": "Polygon", "coordinates": [[[15.34, 46.99], [15.53, 46.99],
                                                                              [15.53, 47.13], [15.34, 47.13],
                                                                              [15.34, 46.99]]]})}])
    st.advance(seconds=1)
    if customers:  # Fluidra's Austrian customer list, as TWP normalises it (DT.5.1)
        cfg = RunContext(st, "demo", "LOAD", "AT", "POOL_SPECIALIST", BY).config()
        r = load_customers(st, cfg, customers, "AT", "TWP_SHEET_AT", "AT", customers_date, BY)
        print(f"  customers: {r['customers']} loaded from {customers.name}")
        st.advance(seconds=1)
    return st


def _crawler(store) -> Crawler:
    cfg = RunContext(store, "demo", "LOAD", "AT", "POOL_SPECIALIST", BY).config()
    pats = [r["pattern"] for r in sorted(cfg.rows("biz_cfg_page_priority"), key=lambda r: r.get("priority") or 0)]
    return Crawler(CrawlLimits.from_config(cfg), pats, cfg.shared_domains("SITE_BUILDER"))


def _latest(rows, key):
    out = {}
    for r in rows:
        out[r[key]] = r
    return out


def trace(store, items) -> None:
    """One section per place: what Apify returned and what each step made of it."""
    sel = store.select
    norm = {r["place_id"]: r for r in sel("place_norm_out")}
    link = {r["place_id"]: r["business_id"] for r in sel("business_link_out")}
    ev = _latest(sel("rule_evaluation_out"), "business_id")
    web = _latest(sel("web_validation_out"), "business_id")
    score = {}                 # computed in SQL (lgn_f2_score): not available in memory
    val = _latest(sel("biz_lead_validation"), "business_id")      # logical names in the engine
    exported = {r["business_id"] for r in sel("export_lead_out")}
    lines = ["# Demo trace\n", "Each place, step by step. Full rows: `stages/`.\n"]
    for it in items:
        pid = it.get("placeId")
        n, bid = norm.get(pid, {}), link.get(pid)
        lines.append(f"\n## {it.get('title')}\n")
        lines.append(f"1. **Apify (raw)**: categories {it.get('categories')}, website `{it.get('website')}`, "
                     f"phone `{it.get('phone') or it.get('phoneUnformatted')}`, "
                     f"{it.get('reviewsCount')} reviews, rating {it.get('totalScore')}")
        lines.append(f"2. **Normalized**: business_id {bid}, phone `{n.get('phone_e164')}`, domain "
                     f"`{n.get('domain')}`, postcode `{n.get('postal_code')}`")
        e = ev.get(bid, {})
        lines.append(f"3. **Filter**: {e.get('decision')} (stage {e.get('stage') or '-'}, rule {e.get('rule')}, "
                     f"value `{e.get('trigger_value')}`)")
        if bid in web:
            w = web[bid]
            lines.append(f"4. **Website + model**: crawl {w.get('crawl_status')}, validation **{w.get('validation')}**"
                         f" ({w.get('review_reason') or ''}) — {w.get('validation_reason_text') or ''}; type "
                         f"{w.get('company_type')}, brands {w.get('detected_brands')}, contact "
                         f"{w.get('contact_name')} / {w.get('email')}")
        if bid in score:
            s = score[bid]
            lines.append(f"5. **Score**: {s.get('total')} → **{s.get('rating')}** (type, brands, zone, digital, "
                         f"contact, reviews = {list(s.get('breakdown') or [])})")
        if bid in val:
            lines.append(f"6. **Sales Ops (demo: approve the rest)**: {val[bid].get('decision')}")
        if bid in exported:
            lines.append("7. **Exported** to the Salesforce file")
    (OUT / "trace.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"  trace: {(OUT / 'trace.md').relative_to(ROOT)}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--places", nargs="+", default=[str(OUT / "places.json")],
                   help="saved Apify runs (apify_smoke.py --save), e.g. one per term")
    p.add_argument("--term", default="Poolbau", help="the term of items without Apify's searchString")
    p.add_argument("--area", default="601", help="with --inputs: the Austrian district code (e.g. 207)")
    p.add_argument("--geo", type=Path, default=ROOT / "data" / "geo" / "at_bezirke.geojson",
                   help="with --inputs: the Austrian boundaries, for the area's outline")
    p.add_argument("--inputs", type=Path, help="TWP's inputs Excel: its configuration replaces the demo's")
    p.add_argument("--customers", type=Path, help="Fluidra Austria's customer list (TWP's layout, TWP_SHEET_AT)")
    p.add_argument("--customers-date", help="date of that list, YYYY-MM-DD (its file name prefix)")
    a = p.parse_args()
    if not a.inputs and a.area != "601":
        raise SystemExit("--area needs --inputs (the demo's own configuration only has Graz)")
    area = f"AT-{a.area}" if a.inputs else AREA
    items = [it for f in a.places for it in json.loads(Path(f).read_text(encoding="utf-8"))]
    replay = ReplayCapturer(items, a.term)
    terms = list(replay.by_term)
    print(f"{len(items)} places from {len(a.places)} file(s); terms {terms}; area {area}")
    shutil.rmtree(OUT / "stages", ignore_errors=True)
    shutil.rmtree(OUT / "export", ignore_errors=True)

    st = _store(a.inputs, a.customers, a.customers_date, a.area, a.geo)
    stages = Stages(st)
    stages.mark()

    ctx, cap = run_capture(st, replay,
                           CaptureRequest("AT", "POOL_SPECIALIST", [area], terms, BY,
                                          max(len(v) for v in replay.by_term.values()) if items else 1,
                                          approve_stale_extract=True))
    stages.dump(STAGES[0])
    print(f"  capture: found {cap.get('found')}, pending enrichment {cap.get('pending_enrichment')}")
    if ctx.state == "FAILED":
        print("  capture failed:", ctx.log)
        return 1

    try:
        ectx, enr = run_enrich(st, HandProvider(), _crawler(st),
                               EnrichRequest(ctx.capture_batch_id, BY, llm_mode="DIRECT"))
    except PendingAnswers as e:
        stages.dump(STAGES[1])
        trace(st, items)
        print(f"\n{len(e.args[0])} prompts to answer in {(OUT / 'prompts').relative_to(ROOT)}/:")
        for cid in e.args[0]:
            print(f"  {cid}.txt  ->  tmp/demo/answers/{cid}.json")
        print("Then run this again.")
        return 0
    stages.dump(STAGES[1])
    print(f"  enrich: {ectx.state}, scored {enr.get('scored')}")

    approved = approve_rest(st, ctx.capture_batch_id, BY)
    stages.dump(STAGES[2])
    print(f"  validation: {approved} approved (demo: the whole batch)")

    xctx, xs = run_export(st, ctx.capture_batch_id, BY, OUT / "export")
    stages.dump(STAGES[3])
    print(f"  export: {xs.get('exported')} leads -> {xs.get('file')}")
    if xctx.state == "FAILED":
        print("  export failed:", xctx.log)

    trace(st, items)
    from leadgen.normalize import normalize_category
    ruled = {r["category_normalized"] for r in st.select("biz_cfg_category_rule")}
    seen = {c for p in st.select("place_norm_out") for c in (p.get("categories") or [])
            if normalize_category(c) not in ruled}
    if seen:
        print(f"\nUnclassified Google categories (filtered out as DEFAULT_EXCLUDE): {sorted(seen)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
