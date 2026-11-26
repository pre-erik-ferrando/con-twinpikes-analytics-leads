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


def test_sql_exclusions_sf_status_and_categories_equal_the_reference(ch, tmp_path):
    """lgn_d_sf_status_row, lgn_d_sf_status, lgn_f_exclusion (read by the engine) and lgn_v_category_pending
    answer as their Python references (tests/sql_reference.py, normalize_category) on the same data."""
    from leadgen.loaders import load_sf_status
    from leadgen.normalize import normalize_category
    from leadgen.state import RunContext
    from leadgen.steps.common import subregion_languages
    from leadgen.tests.sql_reference import REFERENCES

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
    # the month in every format normalize_month reads: SQL must give the same YYYY-MM
    nov.write_text(head + f"{codes[0]},00Q1, Huber ,Discarded,11/2026,999,Already Fluidra client\n"
                   f"{codes[1]},,X,Working,202611, - ,\nAT-NL-002,,Old,Working,2026-11-01 00:00:00,501,\n"
                   f"{codes[1]},,X,Lost,01.11.2026,,\n", encoding="utf-8")
    dec.write_text(head + f"{codes[0]},,X,Working,2026-12,,\n{codes[1]},,X,Discarded,12.2026,,\n", encoding="utf-8")
    res = load_sf_status(ch, cfg, nov, "ops")
    assert res["unknown_lead_codes"] == ["AT-NL-002"] and res["unmapped_status"] == [codes[1]]
    assert res["zone_mismatch"] == [codes[0]] and res["loaded"] == 2 and res["countries"] == ["AT"]
    load_sf_status(ch, cfg, dec, "ops")

    cols = {"lgn_d_sf_status_row": ("status_file_id", "row_no", "lead_code", "business_id", "country", "status",
                                    "status_month", "subregion_code", "discard_reason", "company_name", "issue",
                                    "zone_mismatch"),
            "lgn_d_sf_status": ("lead_code", "status_month", "business_id", "status", "discard_reason",
                                "subregion_code", "company_name", "is_current"),
            "lgn_f_exclusion": ("business_id", "country", "origin", "reason", "scope")}
    for view, cs in cols.items():
        sql = {tuple(r[c] for c in cs) for r in ch.read_view(view)}
        ref = {tuple(r[c] for c in cs) for r in REFERENCES[view](ch)}
        assert sql == ref, f"{view}: only SQL {sql - ref}; only reference {ref - sql}"
    excl = ch.read_view("lgn_f_exclusion", {"country": "AT"})
    assert {r["origin"] for r in excl} == {"HUMAN", "SALESFORCE"} and len(excl) == 3   # Nov and Dec discards stay

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


def test_sql_filter_decides_as_the_reference(ch):
    """lgn_f_rule_evaluation (the engine's filter, SQL) against tests/sql_reference.py on every business
    of the earlier tests plus synthetic ones that reach each of the eight steps, with umlauts, symbols,
    two listings per business and a second profile."""
    from leadgen.steps.capture import filter_texts
    from leadgen.tests.sql_reference import reference_decisions

    rules = [r for r in ch.select("biz_cfg_category_rule") if r.get("active", 1)]
    cat = {c: next(r["category_normalized"] for r in rules if r["classification"] == c)
           for c in ("INCLUDE", "CONDITIONAL", "EXCLUDE")}
    titles = [r for r in ch.select("biz_cfg_title_rule") if r.get("active", 1)]
    hard = next(r["pattern"] for r in titles if r["rule_class"] == "HARD")
    soft = next(r["pattern"] for r in titles if r["rule_class"] == "SOFT")
    stem = sorted(r["value"] for r in ch.select("biz_cfg_word_list", {"list_name": "POOL_SIGNAL"}))[0]
    cases = [  # name, categories, owner description, closed permanently, closed temporarily
        ("Geschlossen Pool", [cat["INCLUDE"]], None, 1, 0),
        ("Unterdrückt GmbH", [cat["INCLUDE"]], None, 0, 0),
        ("Früher Ausgeschlossen", [cat["INCLUDE"]], None, 0, 0),
        (f"{hard} Filiale", [cat["INCLUDE"]], None, 0, 0),
        ("Müller & Söhne", [cat["EXCLUDE"], cat["INCLUDE"]], None, 0, 1),
        (f"{stem.capitalize()}technik Weiß", [cat["CONDITIONAL"]], None, 0, 0),
        ("Bau Huber", [cat["CONDITIONAL"]], f"Wir bauen {stem}s und Teiche", 0, 0),
        ("Bau Maier", [cat["CONDITIONAL"]], "Häuser", 0, 0),
        (f"{soft} am See", [cat["CONDITIONAL"]], None, 0, 0),
        ("Hôtel Café", [cat["EXCLUDE"].upper()], None, 0, 0),
        ("Nails Studio", ["Nagelstudio", "  "], None, 0, 0),
        ("e.U. Šimek", [], None, 0, 0),
    ]
    places, links, batch = [], [], []
    for i, (name, cats, owner, closed, temp) in enumerate(cases):
        bid = 7_700_000 + i
        for k in range(2 if i == 4 else 1):     # one business with two listings, only one closed temporarily
            pid = f"syn_{i}_{k}"
            places.append({"run_id": "syn", "place_id": pid, "country": "AT", "name": name, "name_key_ae": "",
                           "name_key_a": "", "subregion_code": "501", "categories": cats, "owner_description": owner,
                           "phone_e164": f"+4366200{i:04d}", "is_closed_permanently": closed,
                           "is_closed_temporarily": temp if k == 0 else 0, **filter_texts(name, owner, cats)})
            links.append({"place_id": pid, "business_id": bid, "country": "AT", "link_reason": "NEW", "run_id": "syn"})
        batch.append({"capture_batch_id": "cb_syn", "business_id": bid, "country": "AT",
                      "profile": "POOL_SPECIALIST", "run_id": "syn"})
    batch.append({**batch[7], "profile": "HVAC"})                 # the same business in a second profile
    ch.insert("place_norm_out", places)
    ch.insert("business_link_out", links)
    ch.insert("capture_batch_lead", batch)
    ch.insert("biz_suppression_request", [{"phone": "+43662000001", "reason": "test", "requested_by": "ops"}])
    ch.insert("biz_lead_validation", [{"business_id": 7_700_002, "profile": "POOL_SPECIALIST", "country": "AT",
                                       "run_id": "syn", "capture_batch_id": "cb_syn", "decision": "EXCLUDED",
                                       "exclusion_reason": "NOT_VALID", "approval_scope": "LEAD", "reviewed_by": "ops"}])

    ref = reference_decisions(ch, "AT", None, any_profile=True)
    sql = ch.filter_decisions("AT", None, any_profile=True)
    assert set(sql) == set(ref)
    diff = [f"{k}: sql {[sql[k][c] for c in ('decision', 'stage', 'rule', 'trigger_value', 'is_closed_temporarily')]}"
            f" != ref {[ref[k][c] for c in ('decision', 'stage', 'rule', 'trigger_value', 'is_closed_temporarily')]}"
            for k in sorted(ref, key=str) if sql[k] != ref[k]]
    assert not diff, "\n".join(diff)
    steps = {ref[(7_700_000 + i, "POOL_SPECIALIST")]["stage"] or ref[(7_700_000 + i, "POOL_SPECIALIST")]["rule"]
             for i in range(len(cases))}
    assert steps >= {"CLOSED", "SUPPRESSED", "PREVIOUSLY_EXCLUDED", "TITLE_RULE", "CATEGORY_INCLUDE",
                     "CATEGORY_CONDITIONAL_POOL_SIGNAL", "CATEGORY_RULE", "DEFAULT_EXCLUDE"}
    maier = ref[(7_700_007, "POOL_SPECIALIST")]       # a CONDITIONAL category without its pool word says so
    assert (maier["rule"], maier["trigger_value"]) == ("CATEGORY_CONDITIONAL_NO_POOL_SIGNAL", cat["CONDITIONAL"])
    assert ref[(7_700_011, "POOL_SPECIALIST")]["rule"] == "NO_RULE_FIRED"


def test_a_failed_export_does_not_freeze_the_score(ch):
    """An export confirmed FAILED leaves the lead exportable again: lgn_f_score shows its live score."""
    import time
    exported = {b for (b,) in ch.client.query("SELECT business_id FROM fld_bizlgn.export_lead_out").result_rows}
    bid, country, live = next((int(b), c, round(float(t), 2)) for b, c, t in ch.client.query(
        "SELECT business_id, country, total FROM fld_bizlgn.lgn_f2_score WHERE profile = 'POOL_SPECIALIST'"
        " ORDER BY business_id").result_rows if int(b) not in exported)
    row = {"export_id": "exp_failed", "business_id": bid, "country": country, "profile": "POOL_SPECIALIST",
           "capture_batch_id": "cb_t", "lead_code": f"{country}-{bid}", "generated_by": "t", "total": 99.0,
           "rating": "HOT", "breakdown": [99.0]}
    ch.insert("export_lead_out", [{**row, "status": "GENERATED"}])
    time.sleep(0.05)
    ch.insert("export_lead_out", [{**row, "status": "FAILED"}])
    shown = {int(b): (round(float(t), 2), src) for b, t, src in ch.client.query(
        "SELECT business_id, total, score_source FROM fld_bizlgn.lgn_f_score").result_rows}
    assert shown[bid] == (live, "LIVE")



def test_sql_customer_match_decides_as_the_engine_after_a_retraction(ch):
    """A Sales Ops customer correction, then its retraction (the lead reopened): lgn_f_customer_match and
    steps.common.deciding_matches pick the same row for every business."""
    import time
    from leadgen.steps.common import deciding_matches
    ids = [int(b) for (b,) in ch.client.query(
        "SELECT business_id FROM fld_bizlgn.lgn_f2_score WHERE country = 'AT' ORDER BY business_id LIMIT 2").result_rows]
    row = {"run_id": "t", "country": "AT", "profile": "POOL_SPECIALIST", "match_pass": "MANUAL", "origin": "HUMAN",
           "signal": "HUMAN", "customer_code": "C1"}
    ch.insert("customer_match_out", [{**row, "business_id": b, "outcome": "CUSTOMER"} for b in ids])
    time.sleep(0.05)
    ch.insert("customer_match_out", [{**row, "business_id": ids[0], "outcome": "RETRACTED"}])
    sql = {int(b): o for b, o in ch.client.query(
        "SELECT business_id, outcome FROM fld_bizlgn.lgn_f_customer_match WHERE country = 'AT'").result_rows}
    py = {b: r["outcome"] for b, r in deciding_matches(ch, "AT").items() if r}
    assert sql[ids[1]] == "CUSTOMER" and sql.get(ids[0]) != "CUSTOMER"
    assert sql == py


def test_sql_validation_and_alias_decide_as_the_engine_after_a_duplicate(ch):
    """A DUPLICATE rejection merged (ALIAS), and a second one merged then reopened (UNALIAS):
    lgn_d_business_alias and lgn_f_validation answer as state.aliases and state.current_validations."""
    import time
    from leadgen.state import aliases, current_validations
    a, b, c, d = [int(x) for (x,) in ch.client.query(
        "SELECT business_id FROM fld_bizlgn.lgn_f2_score WHERE country = 'DE' ORDER BY business_id LIMIT 4").result_rows]
    val = {"profile": "POOL_SPECIALIST", "country": "DE", "run_id": "t", "capture_batch_id": "cb_t",
           "decision": "EXCLUDED", "exclusion_reason": "DUPLICATE", "approval_scope": "LEAD", "reviewed_by": "ops"}
    ch.insert("biz_lead_validation", [{**val, "business_id": a, "duplicate_of": b},
                                      {**val, "business_id": c, "duplicate_of": d}])
    ch.insert("business_identity_out", [{"kind": "ALIAS", "business_id": x, "other_business_id": y,
                                         "reason": "HUMAN_DUPLICATE", "run_id": "t"} for x, y in ((a, b), (c, d))])
    time.sleep(0.05)
    ch.insert("business_identity_out", [{"kind": "UNALIAS", "business_id": c, "reason": "HUMAN_REOPEN",
                                         "run_id": "t"}])
    sql_al = {int(o): int(s) for o, s in ch.client.query(
        "SELECT old_id, surviving_id FROM fld_crplgn.lgn_d_business_alias").result_rows}
    assert sql_al[a] == b and c not in sql_al and sql_al == aliases(ch)
    sql_val = {int(x): (dec, r) for x, dec, r in ch.client.query(
        "SELECT business_id, decision, exclusion_reason FROM fld_bizlgn.lgn_f_validation WHERE country = 'DE'"
        ).result_rows}
    py_val = {x: (v["decision"], v.get("exclusion_reason")) for x, v in current_validations(ch, "DE").items()}
    assert b not in sql_val and sql_val[c] == ("EXCLUDED", "DUPLICATE") and sql_val == py_val


def test_sql_follows_merges_as_the_engine(ch):
    """Chains and a cycle resolve as identity.resolve_alias; after merging the two exported Austrian leads
    (both with Salesforce statuses), exports, matches and analyses follow the surviving business, and
    lgn_v_lead keeps one row per lead."""
    from leadgen.identity import resolve_alias
    from leadgen.state import aliases, exported
    from leadgen.steps.common import current_web, deciding_matches
    chain = [(91, 92), (92, 93), (93, 94), (81, 82), (82, 81)]          # 91 -> 94 in three links; 81 <-> 82
    ch.insert("business_identity_out", [{"kind": "ALIAS", "business_id": a, "other_business_id": b,
                                         "reason": "TEST", "run_id": "t"} for a, b in chain])
    al = aliases(ch)
    sql_al = {int(o): int(s) for o, s in ch.client.query(
        "SELECT old_id, surviving_id FROM fld_crplgn.lgn_d_business_alias").result_rows}
    assert sql_al == {o: resolve_alias(o, al) for o in al} and sql_al[91] == 94
    a, b = sorted({int(b) for (b,) in ch.client.query(
        "SELECT business_id FROM fld_bizlgn.export_lead_out WHERE country = 'AT' AND export_id != 'exp_failed'"
        ).result_rows})[:2]
    ch.insert("business_identity_out", [{"kind": "ALIAS", "business_id": a, "other_business_id": b,
                                         "reason": "TEST", "run_id": "t"}])
    q = ch.client.query
    assert q("SELECT count() FROM fld_bizlgn.lgn_v_lead GROUP BY pk_business_id, pk_profile "
             "HAVING count() > 1").result_rows == []
    assert q(f"SELECT count() FROM fld_bizlgn.lgn_v_lead WHERE pk_business_id = {a}").result_rows == [(0,)]
    sql_exp = {(int(x), e) for x, e in q("SELECT business_id, export_id FROM fld_bizlgn.lgn_f_export_lead "
                                         "WHERE country = 'AT'").result_rows}
    assert sql_exp == set(exported(ch, "AT"))
    sql_m = {int(x): o for x, o in q("SELECT business_id, outcome FROM fld_bizlgn.lgn_f_customer_match "
                                     "WHERE country = 'AT'").result_rows}
    assert sql_m == {x: r["outcome"] for x, r in deciding_matches(ch, "AT").items() if r}
    sql_w = {int(x): v for x, v in q("SELECT business_id, validation FROM fld_crplgn.lgn_d_enrichment "
                                     "WHERE country = 'AT' AND profile = 'POOL_SPECIALIST'").result_rows}
    assert sql_w == {x: r["validation"] for x, r in current_web(ch, "AT", "POOL_SPECIALIST").items()}
