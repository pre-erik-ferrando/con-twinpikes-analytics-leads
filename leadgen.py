"""Lead Generator DACH: the orchestrator's command line (review DT.9: until Intelek can launch runs and
upload files, everything is launched from here and the results are read in the lgn_v_* views).

    python leadgen.py init                       # databases, tables and *_current views (idempotent)
    python leadgen.py seed                       # seeds/*.csv into biz_cfg_* (only keys not yet there)
    python leadgen.py load-config FILE.xlsx --version v4-261020
    python leadgen.py load-geo FILE.geojson --country AT --level SUBREGION --source STATISTIK_AUSTRIA \
        --version 2026 --code-prop g_id --name-prop g_name   # capture sends each area as this outline
    python leadgen.py geo-convert DE_VG250.gpkg de_kreise.geojson --crs 25832 \
        --sql "SELECT geom, AGS, GEN FROM vg250_krs WHERE GF = 4"   # DE/CH packages -> load-geo input
    python leadgen.py load-customers FILE --country AT --source-system TWP_SHEET_AT --extract-date 2026-11-01
    python leadgen.py precheck --country AT --profile POOL_SPECIALIST --areas AT-501 --terms Poolbau
    python leadgen.py capture --country AT --profile POOL_SPECIALIST --areas AT-501,AT-502 --terms Poolbau,Poolshop
    python leadgen.py enrich --capture-batch cb_… [--mode AUTO|BATCH|DIRECT] [--max-leads N] [--reread]
    python leadgen.py approve-cost RUN_ID --amount 25          # then: python leadgen.py resume RUN_ID
    python leadgen.py resume RUN_ID                             # cost approved, or batches finished
    python leadgen.py review --capture-batch cb_…               # leads To review
    python leadgen.py reject --capture-batch cb_… --business-id N --reason KA_CHAIN
    python leadgen.py approve-rest --capture-batch cb_… [--yes]
    python leadgen.py export --capture-batch cb_… --out exports/
    python leadgen.py confirm-export exp_… --status UPLOADED
    python leadgen.py load-sf-status lgn_sf_status_YYYYMM.csv  # Salesforce's monthly status file (DT.8.4)
    python leadgen.py rematch --country AT                      # after a new customer extract
    python leadgen.py reprocess --country AT --profile POOL_SPECIALIST [--capture-batch cb_…]
    python leadgen.py renormalize --country AT                  # the filter's texts again, after a change in normalize.py
    python leadgen.py status [RUN_ID]
    python leadgen.py dbt [-- <dbt args>]                       # dbt build of the views (src/stg/crp/biz)

Credentials from the environment or `.env` next to this file (an exported variable wins):
DBT_CH_HOST, DBT_CH_PORT, DBT_CH_USER, DBT_CH_PASSWORD, DBT_CH_SCHEMA (= fld_landinglgn), APIFY_TOKEN,
OPENAI_API_KEY or ANTHROPIC_API_KEY (per llm_provider). The user running it: LEADGEN_USER (default: the OS user).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "dwh" / "transf_py"))

from leadgen import ddl, runs, seed_loader  # noqa: E402
from leadgen import loaders  # noqa: E402
from leadgen.config import precheck  # noqa: E402
from leadgen.state import RunContext  # noqa: E402

DBT_DIR = ROOT / "dwh" / "transf_dbt"


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value and value[0] in "\"'" and value.find(value[0], 1) > 0:
            value = value[1:value.find(value[0], 1)]       # quoted: up to the closing quote
        else:
            # unquoted: drop an inline comment, also when the value is empty ("KEY=   # comment")
            value = "" if value.startswith("#") else value.split(" #", 1)[0].split("\t#", 1)[0].strip()
        if key and not os.environ.get(key):
            os.environ[key] = value


def store():
    from leadgen.store import ClickHouseStore
    return ClickHouseStore()


def user(ns) -> str:
    return ns.by or os.environ.get("LEADGEN_USER") or getpass.getuser()


def show(obj) -> None:
    print(json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def _cfg(st, country, profile="POOL_SPECIALIST"):
    return RunContext(st, "cli", "CLI", country, profile, "cli").config()


def _crawler(cfg):
    from leadgen.crawl import Crawler, CrawlLimits, playwright_renderer
    pats = cfg.page_patterns()
    return Crawler(CrawlLimits.from_config(cfg), pats, cfg.shared_domains("SITE_BUILDER"), renderer=playwright_renderer)


def cmd(ns) -> int:
    c = ns.command
    if c == "dbt":
        env = {**os.environ, "DBT_CH_SCHEMA": os.environ.get("DBT_CH_SCHEMA", ddl.DB["landing"])}
        args = ns.rest or ["build"]
        return subprocess.call(["dbt", *args, "--profiles-dir", "."], cwd=DBT_DIR, env=env)
    if c == "geo-convert":                                      # files only, no database
        from leadgen import geo_convert
        print(f"{geo_convert.write(Path(ns.file), Path(ns.out), ns.sql, int(ns.crs))} areas written to {ns.out}")
        return 0

    st = store()
    if c == "init":
        for s in st.init():
            print("ok", s)
        return 0
    if c == "seed":
        show(seed_loader.load_csv_seeds(st, user(ns), ns.overwrite))
        return 0
    if c == "load-config":
        show(seed_loader.load_excel(st, Path(ns.file), ns.version, user(ns), ns.overwrite))
        return 0
    if c == "load-geo":
        keep = {a["admin_code"] for a in _cfg(st, ns.country).search_areas().values()
                if a.get("level") == ns.level and a.get("admin_code")}
        if not keep:
            print(f"warning: no {ns.level} areas for {ns.country} in biz_cfg_search_area (run load-config first);"
                  " loading every feature")
        r = loaders.load_geo(st, Path(ns.file), ns.country, ns.level, ns.source, ns.version, ns.code_prop,
                             ns.name_prop, ns.parent_prop, keep or None)
        print(f"{r['loaded']} areas loaded")
        if r["skipped"]:
            print(f"skipped, not among TWP's areas: {', '.join(r['skipped'])}")
        if r["missing"]:
            print(f"warning: TWP's areas with no boundary in the file: {', '.join(r['missing'])}")
        return 0
    if c == "load-customers":
        cfg = _cfg(st, ns.country)
        show(loaders.load_customers(st, cfg, Path(ns.file), ns.country, ns.source_system, ns.subsidiary,
                                    ns.extract_date, user(ns), ns.sheet or 0))
        return 0
    if c == "load-sf-status":
        # Until step 0 reads the shared folder by itself (v3.3 DT.1.6, [PENDIENTE] which folder and how often).
        from leadgen.steps.maintenance import apply_sf_status
        cfg = _cfg(st, None)
        res = loaders.load_sf_status(st, cfg, Path(ns.file), user(ns))
        rows = loaders.sf_status_rows(st, cfg, [res["status_file_id"]])[0]
        res["new_customer_matches"] = {cc: apply_sf_status(st, cc) for cc in sorted({r["country"] for r in rows})}
        show(res)
        return 0
    if c == "precheck":
        cfg = _cfg(st, ns.country, ns.profile)
        res = precheck(cfg, ns.areas.split(",") if ns.areas else None, ns.terms.split(",") if ns.terms else None)
        from leadgen.store import ENGINE_VIEWS
        missing = st.missing_views(ENGINE_VIEWS)
        if missing:
            res.blocking.append(f"the engine's views {', '.join(missing)} are not in the database: run "
                                "`python leadgen.py dbt`")
        show({"blocking": res.blocking, "warnings": res.warnings})
        return 1 if res.blocking else 0
    if c in ("capture", "import-list"):
        from leadgen.apify import ApifyCapturer
        from leadgen.steps.capture import CaptureRequest, run_capture, run_import_list
        cfg = _cfg(st, ns.country, ns.profile)
        cap = ApifyCapturer(os.environ["APIFY_TOKEN"], cfg.param("apify_actor_build") or "")
        if c == "capture":
            req = CaptureRequest(ns.country, ns.profile, ns.areas.split(","), ns.terms.split(","), user(ns),
                                 ns.max_places, ns.subdivide, approve_stale_extract=ns.approve_stale_extract,
                                 approve_cost_usd=ns.approve_cost)
            ctx, summary = run_capture(st, cap, req)
        else:
            file_id, rows = loaders.load_import_list(st, Path(ns.file), ns.country, ns.profile, user(ns))
            req = CaptureRequest(ns.country, ns.profile, [], [], user(ns), import_rows=rows, import_file_id=file_id,
                                 approve_cost_usd=ns.approve_cost)
            ctx, summary = run_import_list(st, cap, req)
        show({"run_id": ctx.run_id, **summary, "log": ctx.log})
        return 0
    if c in ("enrich", "resume"):
        from leadgen.llm import make_provider
        from leadgen.steps.enrich import EnrichRequest, resume_enrich, run_enrich
        if c == "enrich":
            b = st.select("capture_batch", {"capture_batch_id": ns.capture_batch})
            if not b:
                print(f"capture batch {ns.capture_batch} not found", file=sys.stderr)
                return 2
            cfg = _cfg(st, b[0]["country"], b[0]["profile"])
            ctx, summary = run_enrich(st, make_provider(cfg), _crawler(cfg),
                                      EnrichRequest(ns.capture_batch, user(ns), ns.mode, ns.max_leads, ns.reread))
        else:
            rows = st.select("pipeline_run", {"run_id": ns.run_id})
            if not rows:
                print(f"run {ns.run_id} not found", file=sys.stderr)
                return 2
            cfg = _cfg(st, rows[0]["country"], rows[0]["profile"])
            ctx, summary = resume_enrich(st, make_provider(cfg), _crawler(cfg), ns.run_id)
        show({**summary, "log": ctx.log})
        return 0
    if c == "approve-cost":
        st.insert("biz_run_approval", [{"run_id": ns.run_id, "approval_type": ns.type,
                                        "approved_amount_usd": ns.amount, "comment": ns.comment,
                                        "approved_by": user(ns)}])
        print(f"{ns.type} approved for {ns.run_id}; resume it with: python leadgen.py resume {ns.run_id}")
        return 0
    if c == "review":
        from leadgen.steps.maintenance import to_review
        ids = to_review(st, ns.capture_batch)
        b = st.select("capture_batch", {"capture_batch_id": ns.capture_batch})[0]
        scores = st.lead_scores(b["country"], b["profile"], ids) if ids else {}
        for b in ids:
            s = scores.get(b, {})
            print(f"{b}\t{s.get('total')}\t{s.get('rating')}")
        print(f"{len(ids)} leads To review")
        return 0
    if c == "approve-rest":
        from leadgen.steps.maintenance import approve_rest, to_review
        n = len(to_review(st, ns.capture_batch))
        if not ns.yes:
            print(f"{n} leads would be approved (Approve the rest of the batch). Repeat with --yes.")
            return 0
        print(f"{approve_rest(st, ns.capture_batch, user(ns))} leads approved")
        return 0
    if c == "reject":
        from leadgen.steps.maintenance import reject
        reject(st, ns.capture_batch, ns.business_id, ns.reason, user(ns), ns.customer_code, ns.subsidiary,
               ns.duplicate_of, ns.comment)
        return 0
    if c == "reopen":
        from leadgen.steps.maintenance import reopen
        reopen(st, ns.capture_batch, ns.business_id, user(ns))
        return 0
    if c == "export":
        from leadgen.steps.export import run_export
        ctx, summary = run_export(st, ns.capture_batch, user(ns), Path(ns.out))
        show(summary)
        return 0 if "error" not in summary else 1
    if c == "confirm-export":
        from leadgen.steps.export import confirm
        print(f"{confirm(st, ns.export_id, ns.status, user(ns))} leads marked {ns.status}")
        return 0
    if c == "reprocess":
        from leadgen.steps.maintenance import run_reprocess
        ctx, summary = run_reprocess(st, ns.country, ns.profile, user(ns), ns.capture_batch)
        show({"run_id": ctx.run_id, **summary})
        return 0
    if c == "rematch":
        from leadgen.steps.maintenance import run_rematch
        ctx, summary = run_rematch(st, ns.country, user(ns))
        show({"run_id": ctx.run_id, **summary})
        return 0
    if c == "renormalize":
        from leadgen.steps.maintenance import renormalize_places
        print(f"{renormalize_places(st, ns.country)} listings renormalized")
        return 0
    if c == "apply-sf-status":
        from leadgen.steps.maintenance import apply_sf_status
        print(f"{apply_sf_status(st, ns.country)} new Salesforce customer matches")
        return 0
    if c == "status":
        states = runs.current_states(st.select("pipeline_run", {"run_id": ns.run_id} if ns.run_id else None))
        for r in sorted(states.values(), key=lambda r: r["inserted_at"]):
            print(f"{r['run_id']}\t{r['run_type']}\t{r['state']}\t{r['country']}\t{r.get('capture_batch_id') or ''}"
                  f"\t{(r.get('message') or '')[:120]}")
        return 0
    raise SystemExit(f"unknown command {c}")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--by", help="who runs it (default LEADGEN_USER or the OS user)")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    s = sub.add_parser("seed"); s.add_argument("--overwrite", action="store_true")
    s = sub.add_parser("load-config"); s.add_argument("file"); s.add_argument("--version", required=True)
    s.add_argument("--overwrite", action="store_true")
    s = sub.add_parser("load-geo")
    for a in ("file",):
        s.add_argument(a)
    for a in ("--country", "--level", "--source", "--version", "--code-prop", "--name-prop"):
        s.add_argument(a, required=True)
    s.add_argument("--parent-prop")
    s = sub.add_parser("geo-convert"); s.add_argument("file"); s.add_argument("out")
    s.add_argument("--crs", required=True, choices=["25832", "2056"]); s.add_argument("--sql", required=True)
    s = sub.add_parser("load-customers"); s.add_argument("file"); s.add_argument("--country", required=True)
    s.add_argument("--source-system", required=True); s.add_argument("--subsidiary")
    s.add_argument("--extract-date"); s.add_argument("--sheet")
    s = sub.add_parser("load-sf-status"); s.add_argument("file")
    s = sub.add_parser("precheck"); s.add_argument("--country", required=True)
    s.add_argument("--profile", default="POOL_SPECIALIST"); s.add_argument("--areas"); s.add_argument("--terms")
    s = sub.add_parser("capture"); s.add_argument("--country", required=True)
    s.add_argument("--profile", default="POOL_SPECIALIST"); s.add_argument("--areas", required=True)
    s.add_argument("--terms", required=True); s.add_argument("--max-places", type=int)
    s.add_argument("--subdivide", action="store_true")
    s.add_argument("--approve-stale-extract", action="store_true",
                   help="run although the customer extract is older than customer_extract_max_age_days")
    s.add_argument("--approve-cost", type=float, metavar="USD",
                   help="approve an Apify estimate above cost_approval_threshold_usd (at least the estimate)")
    s = sub.add_parser("import-list"); s.add_argument("file"); s.add_argument("--country", required=True)
    s.add_argument("--profile", default="POOL_SPECIALIST")
    s.add_argument("--approve-cost", type=float, metavar="USD", help="approve an Apify estimate above the threshold")
    s = sub.add_parser("enrich"); s.add_argument("--capture-batch", required=True)
    s.add_argument("--mode", default="AUTO", choices=["AUTO", "BATCH", "DIRECT"])
    s.add_argument("--max-leads", type=int); s.add_argument("--reread", action="store_true")
    s = sub.add_parser("resume"); s.add_argument("run_id")
    s = sub.add_parser("approve-cost"); s.add_argument("run_id"); s.add_argument("--amount", type=float)
    s.add_argument("--type", default="COST", choices=["COST"]); s.add_argument("--comment")
    s = sub.add_parser("review"); s.add_argument("--capture-batch", required=True)
    s = sub.add_parser("approve-rest"); s.add_argument("--capture-batch", required=True)
    s.add_argument("--yes", action="store_true")
    s = sub.add_parser("reject"); s.add_argument("--capture-batch", required=True)
    s.add_argument("--business-id", type=int, required=True); s.add_argument("--reason", required=True)
    s.add_argument("--customer-code"); s.add_argument("--subsidiary"); s.add_argument("--duplicate-of", type=int)
    s.add_argument("--comment")
    s = sub.add_parser("reopen"); s.add_argument("--capture-batch", required=True)
    s.add_argument("--business-id", type=int, required=True)
    s = sub.add_parser("export"); s.add_argument("--capture-batch", required=True); s.add_argument("--out", default="exports")
    s = sub.add_parser("confirm-export"); s.add_argument("export_id")
    s.add_argument("--status", required=True, choices=["UPLOADED", "FAILED"])
    s = sub.add_parser("reprocess"); s.add_argument("--country", required=True)
    s.add_argument("--profile", default="POOL_SPECIALIST"); s.add_argument("--capture-batch")
    s = sub.add_parser("rematch"); s.add_argument("--country", required=True)
    s = sub.add_parser("apply-sf-status"); s.add_argument("--country", required=True)
    s = sub.add_parser("renormalize"); s.add_argument("--country", required=True)
    s = sub.add_parser("status"); s.add_argument("run_id", nargs="?")
    s = sub.add_parser("dbt"); s.add_argument("rest", nargs=argparse.REMAINDER)
    return p


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    return cmd(parser().parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
