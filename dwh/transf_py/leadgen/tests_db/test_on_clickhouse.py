"""The engine and the dbt views on a real ClickHouse (run_db_tests.sh locally, job `db` in CI).

The no-DB suite runs every step on an in-memory store; this checks what that store cannot: the DDL, the
inserts and reads through clickhouse-connect (types, NULLs, defaults), and every dbt model and test with
real data. It only runs against a throwaway server: it drops and recreates the fld_*lgn databases.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("LEADGEN_TEST_CH") != "1" or os.environ.get("DBT_CH_HOST") != "localhost",
    reason="needs the throwaway ClickHouse of run_db_tests.sh or of CI (it drops the fld_*lgn databases)")

from leadgen import ddl, seed_loader  # noqa: E402
from leadgen.steps.capture import CaptureRequest, run_capture  # noqa: E402
from leadgen.steps.enrich import EnrichRequest, run_enrich  # noqa: E402
from leadgen.steps.export import confirm, run_export  # noqa: E402
from leadgen.steps.maintenance import approve_rest  # noqa: E402
from leadgen.store import ClickHouseStore  # noqa: E402

from ..tests.conftest import SQUARE, FakeCapturer, FakeProvider, _cfg_rows, crawler  # noqa: E402
from ..tests.test_flow import ITEMS  # noqa: E402

DBT_DIR = Path(__file__).resolve().parents[3] / "transf_dbt"


@pytest.fixture(scope="module")
def ch():
    st = ClickHouseStore()
    for db in ddl.DB.values():
        st.command(f"DROP DATABASE IF EXISTS {db}")
    st.init()
    seed_loader.load_csv_seeds(st, "test")
    typed = {t: seed_loader.typed_rows(t, [{k: (str(v) if v is not None else None) for k, v in r.items()}
                                           for r in rows]) for t, rows in _cfg_rows().items()}
    seed_loader.load(st, typed, "test-excel", "test", "synthetic")
    st.insert("reference_geo", [{"source": "TEST", "country": "AT", "version": "1", "admin_level": "SUBREGION",
                                 "admin_code": "501", "admin_name": "Salzburg(Stadt)",
                                 "geometry_json": json.dumps(SQUARE)}])
    for args in (("deps",), ("run",)):          # as in production: the views exist before any run (enrich needs them)
        r = _dbt(*args)
        assert r.returncode == 0, f"dbt {' '.join(args)} failed:\n{r.stdout[-6000:]}"
    return st


def _dbt(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["dbt", *args, "--profiles-dir", "."], cwd=DBT_DIR, env=os.environ.copy(),
                          capture_output=True, text=True)


def test_full_flow_then_every_dbt_model_and_test(ch, tmp_path):
    ctx, cap = run_capture(ch, FakeCapturer(ITEMS),
                           CaptureRequest("AT", "POOL_SPECIALIST", ["AT-501"], ["Poolbau"], "ops", 20))
    assert cap["found"] == 6                                     # as on the in-memory store
    _, enr = run_enrich(ch, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops", llm_mode="DIRECT"))
    assert enr["scored"] >= 1
    approve_rest(ch, ctx.capture_batch_id, "ops")
    _, xs = run_export(ch, ctx.capture_batch_id, "ops", tmp_path)
    assert xs["exported"] >= 1
    confirm(ch, xs["export_id"], "UPLOADED", "ops")

    r = _dbt("build")                                             # every model, then every test, with data
    assert r.returncode == 0, f"dbt build failed:\n{r.stdout[-6000:]}"

    status = dict(ch.client.query("SELECT des_status, count() FROM fld_bizlgn.lgn_v_lead GROUP BY 1").result_rows)
    assert status.get("Published", 0) == xs["exported"]
    sf = ch.client.query("SELECT count() FROM fld_bizlgn.lgn_v_salesforce_export").result_rows[0][0]
    assert sf >= 0

    # The score (computed in SQL) equals the reference of the engine's former Python scoring
    assert _scores(ch, "AT") == _golden("AT")
    huber = next(b for (b,) in ch.client.query(
        "SELECT business_id FROM fld_bizlgn.web_validation_out WHERE domain = 'poolbau-huber.at'").result_rows)
    # type 10, brands G1 7.5, zone ATTACK 7.5, e-commerce 5, person+email+phone 5, 40 reviews > P75 provisional 2.5
    assert _scores(ch, "AT")[huber] == (37.5, "HOT", [10.0, 7.5, 7.5, 5.0, 5.0, 2.5])
    from openpyxl import load_workbook
    ws = load_workbook(xs["file"]).active
    rows = [dict(zip([c.value for c in ws[1]], [c.value for c in r])) for r in ws.iter_rows(min_row=2)]
    assert {r["Rating"] for r in rows if r["Lead code"] == f"AT-{huber}"} == {"Hot"}
    # frozen at export: one row per exported lead, as the live score was then; lgn_f_score shows it
    frozen = {b: (round(float(t), 2), r) for b, t, r in ch.client.query(
        "SELECT business_id, total, rating FROM fld_bizlgn.export_lead_out WHERE isNotNull(total)").result_rows}
    assert frozen[huber] == (37.5, "HOT")
    src = dict(ch.client.query("SELECT business_id, score_source FROM fld_bizlgn.lgn_f_score").result_rows)
    assert src[huber] == "EXPORT" and all(src[b] == "LIVE" for b in _golden("AT") if b not in frozen)


def _scores(ch, country: str) -> dict[int, tuple]:
    return {int(b): (round(float(t), 2), r, [float(x) for x in bd]) for b, t, r, bd in ch.client.query(
        "SELECT business_id, total, rating, breakdown FROM fld_bizlgn.lgn_f2_score WHERE country = %(c)s",
        parameters={"c": country}).result_rows}


def _buckets(ch, country: str) -> dict[tuple, str]:
    return {(int(b), f): bk for b, f, bk in ch.client.query(
        "SELECT business_id, factor, bucket_code FROM fld_bizlgn.lgn_f2_score_component WHERE country = %(c)s",
        parameters={"c": country}).result_rows}


GOLDEN = json.loads((Path(__file__).parent / "golden_scores.json").read_text())


def _golden(country: str) -> dict[int, tuple]:
    return {s["business_id"]: (s["total"], s["rating"], s["breakdown"]) for s in GOLDEN["scores"] if s["country"] == country}


def _load_cfg(ch, rows: dict, version: str) -> None:
    seed_loader.load(ch, {t: seed_loader.typed_rows(t, [{k: (str(v) if v is not None else None) for k, v in r.items()}
                                                        for r in rs]) for t, rs in rows.items()}, version, "test", version)


def _insert_leads(ch, country: str, leads: list[dict]) -> None:
    """One business per lead: listing (place_norm_out), link and checked model answer (web_validation_out)."""
    places, links, webs, matches = [], [], [], []
    for d in leads:
        bid, pid = d["bid"], f"{country.lower()}-p{d['bid']}"
        places.append({"run_id": f"synthetic-{country}", "place_id": pid, "country": country, "name": f"Firm {bid}",
                       "name_key_ae": f"firm {bid}", "name_key_a": f"firm {bid}", "website": d.get("website"),
                       "domain": d.get("domain"), "phone_e164": d.get("phone_google"),
                       "subregion_code": d.get("subregion"), "reviews_count": d.get("reviews"),
                       "is_closed_permanently": 0, "is_closed_temporarily": 0})
        links.append({"place_id": pid, "business_id": bid, "country": country, "link_reason": "NEW",
                      "run_id": f"synthetic-{country}"})
        webs.append({"run_id": f"synthetic-{country}", "business_id": bid, "country": country, "profile": "POOL_SPECIALIST",
                     "validation": d.get("validation", "VALID"), "vat_check_status": "NOT_CHECKED",
                     "company_type": d.get("type"), "detected_brands": d.get("brands", []),
                     "has_ecommerce": d.get("ecommerce"), "crawl_status": d.get("crawl"),
                     "contact_name": d.get("person"), "email": d.get("email"), "phone_e164": d.get("phone_web")})
        if d.get("customer"):
            matches.append({"run_id": f"synthetic-{country}", "business_id": bid, "country": country,
                            "match_pass": "CAPTURE", "origin": "AUTO", "outcome": "CUSTOMER", "signal": "PHONE"})
    ch.insert("place_norm_out", places)
    ch.insert("business_link_out", links)
    ch.insert("customer_match_out", matches)
    ch.insert("web_validation_out", webs)


def test_sql_score_equals_the_reference_on_every_bucket(ch):
    """40 synthetic German leads built to reach every bucket of every factor, with German configuration of
    their own (brand list, zones, a review minimum of 5 so the reference is computed, not provisional) and 5
    customers, who are not scored. Their score must equal the reference (golden_scores.json: what the
    engine's Python scoring gave for them), lead by lead and bucket by bucket."""
    from leadgen.domain import SCORING_BUCKETS
    from leadgen.normalize import registrable_domain

    _load_cfg(ch, {
        "biz_cfg_brand": [{"country": "DE", "brand": "DEPUMP", "owner": "COMPETITOR", "brand_group": 1},
                          {"country": "DE", "brand": "DECHEM", "owner": "COMPETITOR", "brand_group": 2},
                          {"country": "DE", "brand": "DEROBOT", "owner": "COMPETITOR", "brand_group": 3}],
        "biz_cfg_strategic_zone": [{"country": "DE", "admin_level": "SUBREGION", "admin_code": c, "admin_name": c,
                                    "quadrant": q, "model_version": "test"}
                                   for c, q in [("D1", "ATTACK"), ("D2", "ACTIVATE"), ("D3", "DEFEND"), ("D4", "OPTIMIZE")]],
        "biz_cfg_run_parameter": [{"country": "DE", "parameter": "review_reference_min_n", "value": "5",
                                   "value_type": "INT"}],
    }, "test-de")
    builders = {"wixsite.com", "jimdofree.com", "jimdosite.com"}
    websites = [None, "https://www.firm{i}.de/", "https://firm{i}.wixsite.com/pool", "https://www.facebook.com/firm{i}",
                "https://shop.firm{i}.de/"]
    subregions = ["D1", "D2", "D3", "D4", None, "D9"]
    reviews = [None, 0, 1, 3, 8, 15, 40, 120]
    brands = [[], ["DEPUMP"], ["DECHEM"], ["DEROBOT"], ["DEROBOT", "DECHEM"], ["SPECK"], ["DEPUMP", "SPECK"]]
    types = ["BUILDER_INSTALLER_SHOP", "MAINTENANCE_OR_PREFAB", "WEAK_UNVERIFIED", None]
    crawl = ["OK", "NO_WEBSITE", "SOCIAL_ONLY", "UNREACHABLE", None]
    leads = []
    for i in range(40):
        site = websites[i % 5]
        site = site.format(i=i) if site else None
        # moduli chosen so the factors do not move in step (every bucket is reached)
        leads.append({"bid": 9_000_000 + i, "website": site, "domain": registrable_domain(site, builders) if site else None,
                      "phone_google": None if i % 5 == 0 else f"+4930{1000 + i}", "subregion": subregions[i % 6],
                      "reviews": reviews[i % 8], "validation": ["VALID", "VALID", "REVIEW", "VALID", "NOT_VALID"][i % 5],
                      "type": types[i % 4], "brands": brands[i % 7], "ecommerce": [1, 0, None][i % 3],
                      "crawl": crawl[(i // 5) % 5], "person": "Max" if i % 3 == 0 else None,
                      "email": "a@b.de" if i % 4 != 2 else None, "phone_web": "+49301234" if i % 6 == 0 else None,
                      "customer": i % 7 == 0})
    _insert_leads(ch, "DE", leads)
    r = _dbt("run", "--select", "+lgn_f_score")
    assert r.returncode == 0, r.stdout[-6000:]

    golden = _golden("DE")
    assert len(golden) == 27 and _scores(ch, "DE") == golden     # 40 leads: 8 NOT_VALID, 5 customers not scored
    gb = {(b["business_id"], b["factor"]): b["bucket_code"] for b in GOLDEN["buckets"] if b["business_id"] in golden}
    sqlb = _buckets(ch, "DE")
    assert sqlb == gb
    seen = {(f, bk) for (_, f), bk in sqlb.items()}
    missing = [(f, bk) for f, bks in SCORING_BUCKETS.items() for bk in bks if (f, bk) not in seen]
    assert not missing, f"buckets the synthetic leads never reach: {missing}"
    assert {r for _, r, _ in golden.values()} == {"HOT", "WARM", "COLD"}


def test_definition_examples(ch):
    """The definition's own examples, on leads made for them (Switzerland, Belgium, the Netherlands)."""
    _load_cfg(ch, {
        "biz_cfg_strategic_zone": [{"country": "CH", "admin_level": "SUBREGION", "admin_code": "C1", "admin_name": "C1",
                                    "quadrant": "ATTACK", "model_version": "test"}],
        "biz_cfg_run_parameter": [{"country": c, "parameter": "review_reference_min_n", "value": "1", "value_type": "INT"}
                                  for c in ("NL", "BE")],
    }, "test-examples")
    full = {"type": "BUILDER_INSTALLER_SHOP", "brands": ["SPECK"], "subregion": "C1", "ecommerce": 0,
            "website": "https://x.ch/", "domain": "x.ch", "crawl": "OK", "person": "Max", "email": "a@x.ch",
            "phone_web": "+41", "reviews": 5}
    ch_leads = [
        {"bid": 8_000_001, **full},                                               # the cockpit example
        {"bid": 8_000_002, **full, "validation": "REVIEW", "reviews": 35},
        {"bid": 8_000_003, **full, "person": None},
        {"bid": 8_000_004, **full, "person": None, "email": None, "phone_web": None, "phone_google": "+41"},
        {"bid": 8_000_005, **full, "crawl": "UNREACHABLE"},
        {"bid": 8_000_006, **full, "crawl": "SOCIAL_ONLY"},
        {"bid": 8_000_007, **full, "reviews": None},
        {"bid": 8_000_008, **full, "ecommerce": 1, "person": None, "email": None, "phone_web": None, "reviews": None},
        {"bid": 8_000_009, **full, "email": None, "phone_web": None, "person": None, "phone_google": "+41"},
        {"bid": 8_000_010, "type": "WEAK_UNVERIFIED"},
    ]
    nl = [{"bid": 8_100_000 + n, "reviews": n} for n in (10, 20, 30, 40)] + [
        {"bid": 8_100_133, "reviews": 33, "validation": "REVIEW"}, {"bid": 8_100_131, "reviews": 31, "validation": "REVIEW"}]
    be = [{"bid": 8_200_000 + i, "reviews": 2} for i in range(4)]
    _insert_leads(ch, "CH", ch_leads)
    _insert_leads(ch, "NL", nl)
    _insert_leads(ch, "BE", be)
    r = _dbt("run", "--select", "+lgn_f_score")
    assert r.returncode == 0, r.stdout[-6000:]
    s, b = _scores(ch, "CH"), _buckets(ch, "CH")

    max_points = ch.client.query("SELECT sum(m) FROM (SELECT factor, max(points) AS m FROM fld_stglgn.lgn_a_scoring_rule "
                                 "WHERE isNull(country) GROUP BY factor)").result_rows[0][0]
    assert float(max_points) == 37.5
    # the cockpit's example: 10 + 7.5 + 7.5 + 2.5 + 5 + 0.5 = 33, HOT (provisional review reference 34.8 / 10.5)
    assert s[8_000_001] == (33.0, "HOT", [10.0, 7.5, 7.5, 2.5, 5.0, 0.5])
    # REVIEW forces a weak profile; 35 reviews > P75 34.8 (strictly)
    assert s[8_000_002][2][0] == 2.5 and s[8_000_002][2][5] == 2.5
    assert b[(8_000_003, "CONTACT")] == "EMAIL_PHONE" and b[(8_000_004, "CONTACT")] == "ANY"
    assert b[(8_000_005, "DIGITAL")] == "OWN_WEBSITE"           # a website that does not answer still counts
    assert b[(8_000_006, "DIGITAL")] == "NONE" and b[(8_000_007, "REVIEWS")] == "NONE"
    assert s[8_000_008][:2] == (30.0, "HOT")                     # >= 30 is HOT
    assert s[8_000_009][:2] == (29.0, "WARM")                    # below 30
    assert s[8_000_010][1] == "COLD"
    # PERCENTILE.INC of 10, 20, 30, 40: P75 32.5 and median 25 (another method gives 37.5 or 30)
    nlb = _buckets(ch, "NL")
    assert nlb[(8_100_133, "REVIEWS")] == "ABOVE_P75" and nlb[(8_100_131, "REVIEWS")] == "ABOVE_MEDIAN"
    assert nlb[(8_100_040, "REVIEWS")] == "ABOVE_P75" and nlb[(8_100_020, "REVIEWS")] == "ANY"
    # strictly greater: 2 reviews against a P75 and a median of 2 is just ANY
    assert {bk for (_, f), bk in _buckets(ch, "BE").items() if f == "REVIEWS"} == {"ANY"}


def test_exported_score_stays_frozen_when_the_points_change(ch):
    """After the flow's export, the points of ATTACK change for Austria: the live score of the exported lead
    moves, the one lgn_f_score shows (frozen at export) does not."""
    huber = next(b for (b,) in ch.client.query(
        "SELECT business_id FROM fld_bizlgn.web_validation_out WHERE domain = 'poolbau-huber.at'").result_rows)
    _load_cfg(ch, {"biz_cfg_scoring_rule": [{"country": "AT", "profile": "POOL_SPECIALIST", "factor": "ZONE",
                                             "bucket_code": "ATTACK", "bucket_description": "Attack (test)",
                                             "points": "1"}]}, "test-freeze")
    assert _scores(ch, "AT")[huber][0] == 31.0                   # live: 37.5 - 7.5 + 1
    shown = {int(b): (round(float(t), 2), src) for b, t, src in ch.client.query(
        "SELECT business_id, total, score_source FROM fld_bizlgn.lgn_f_score WHERE country = 'AT'").result_rows}
    assert shown[huber] == (37.5, "EXPORT")


def test_sql_exclusions_sf_status_and_categories_equal_the_engine(ch, tmp_path):
    """lgn_d_sf_status, lgn_f_exclusion and lgn_v_category_pending read the same sources as the engine
    (loaders.sf_status_rows, state.exclusions, normalize_category): same answers on the same data."""
    from leadgen.loaders import load_sf_status, sf_status_rows
    from leadgen.normalize import normalize_category
    from leadgen.state import RunContext, exclusions
    from leadgen.steps.common import subregion_languages

    codes = sorted({r["lead_code"] for r in ch.select("export_lead_out")})
    assert len(codes) >= 2
    cb = ch.select("capture_batch")[0]["capture_batch_id"]
    other = next(r["business_id"] for r in ch.select("capture_batch_lead")
                 if f"AT-{r['business_id']}" not in codes)
    ch.insert("biz_lead_validation", [{"business_id": other, "profile": "POOL_SPECIALIST", "country": "AT",
                                       "run_id": "t", "capture_batch_id": cb, "decision": "EXCLUDED",
                                       "exclusion_reason": "NOT_VALID", "approval_scope": "LEAD", "reviewed_by": "ops"}])
    cfg = RunContext(ch, "t", "LOAD", "AT", "POOL_SPECIALIST", "t").config()
    head = "Lead code,Salesforce ID,Company,Status,Status month,Subregion code,Discard reason\n"
    nov, dec = tmp_path / "s_202611.csv", tmp_path / "s_202612.csv"
    nov.write_text(head + f"{codes[0]},00Q1, Huber ,Discarded,2026-11,501,Already Fluidra client\n"
                   f"{codes[1]},,X,Working,2026-11, - ,\nAT-NL-002,,Old,Working,2026-11,501,\n", encoding="utf-8")
    dec.write_text(head + f"{codes[0]},,X,Working,2026-12,,\n{codes[1]},,X,Discarded,2026-12,,\n", encoding="utf-8")
    load_sf_status(ch, cfg, nov, "ops")
    load_sf_status(ch, cfg, dec, "ops")
    r = _dbt("run")
    assert r.returncode == 0, r.stdout[-4000:]

    month = {}
    for row in sf_status_rows(ch)[0]:
        month[(row["lead_code"], row["status_month"])] = row
    py_sf = {(c, m, r["business_id"], r["status"], r["discard_reason"], r["subregion_code"], r["company_name"])
             for (c, m), r in month.items()}
    sql_sf = {tuple(x) for x in ch.client.query(
        "SELECT lead_code, status_month, business_id, status, discard_reason, subregion_code, company_name "
        "FROM fld_crplgn.lgn_d_sf_status").result_rows}
    assert sql_sf == py_sf and len(py_sf) == 4

    py_ex = {(r["business_id"], r["origin"], r["reason"]) for r in exclusions(ch, "AT")}
    sql_ex = {tuple(x) for x in ch.client.query(
        "SELECT business_id, origin, reason FROM fld_bizlgn.lgn_f_exclusion").result_rows}
    assert sql_ex == py_ex
    assert {o for _, o, _ in py_ex} == {"HUMAN", "SALESFORCE"} and len(py_ex) == 3   # Nov and Dec discards stay

    langs = subregion_languages(cfg)
    rules = [r for r in ch.select("biz_cfg_category_rule") if r.get("active", 1)]
    places = {}
    for p in ch.select("place_norm_out"):
        places[p["place_id"]] = p
    py_cat = set()
    for p in places.values():
        lang = langs.get(p.get("subregion_code") or "")
        ruled = {r["category_normalized"] for r in rules if r.get("country") in (None, p["country"])
                 and (lang is None or r.get("language") in (None, lang))}
        py_cat |= {normalize_category(c) for c in p["categories"] if normalize_category(c) not in ruled}
    sql_cat = {x for (x,) in ch.client.query(
        "SELECT pk_category_normalized FROM fld_bizlgn.lgn_v_category_pending").result_rows}
    assert sql_cat == py_cat - {""}
