"""End to end on the in-memory store: capture -> enrich -> validation -> export -> upload -> call
results -> monthly rematch, as Sales Ops would run them (DF.4)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from leadgen import ddl, prompt, seed_loader
from leadgen.loaders import load_customers
from leadgen.state import RunContext, current_validations, published_ids
from leadgen.steps import maintenance
from leadgen.steps.capture import CaptureRequest, RunFailed, run_capture
from leadgen.steps.enrich import EnrichRequest, resume_enrich, run_enrich
from leadgen.steps.export import confirm, run_export
from leadgen.steps.maintenance import apply_cockpit_outcomes, approve_rest, reject, run_rematch, to_review

from .conftest import SQUARE, FakeCapturer, FakeProvider, _cfg_rows, answer, crawler, item

ITEMS = [
    item("p_huber", "Poolbau Huber", ["Schwimmbadbauer"], website="https://poolbau-huber.at", phone="+43662123456",
         reviews=40),
    item("p_hotel", "Hotel Post", ["Hotel"]),
    item("p_horn", "Hornbach Salzburg", ["Baumarkt", "Schwimmbadzubehörgeschäft"]),
    item("p_bau", "Mayr Bau", ["Bauunternehmen"], description="Wir bauen Pools und Schwimmteiche", reviews=3),
    item("p_cust", "Wasserwelt Kunde", ["Schwimmbadbauer"], phone="+43662999999"),
    item("p_dead", "Altpool", ["Schwimmbadbauer"], closed=True),
]


def _customers(store, tmp_path: Path):
    f = tmp_path / "customers_at.csv"
    f.write_text("customer_number,customer_name_original,Address,postal_code_normalized,city_normalized,country_code,"
                 "phone_normalized,vat_id_normalized,email_normalized,corporate_email_domain,Latitude,Longitude\n"
                 "C100,Wasserwelt GmbH,Weg 1,5020,Salzburg,AT,+43662999999,,,wasserwelt.at,\"47,8\",\"13,0\"\n"
                 "C999,Test Kunde,Weg 2,XXX,Salzburg,AT,0000000,,,,,\n", encoding="utf-8")
    cfg = RunContext(store, "t", "LOAD", "AT", "POOL_SPECIALIST", "t").config()
    return load_customers(store, cfg, f, "AT", "TWP_SHEET_AT", "AT", store.now().date().isoformat(), "test")


def _capture(store, items=ITEMS, **kw):
    return run_capture(store, FakeCapturer(items),
                       CaptureRequest("AT", "POOL_SPECIALIST", ["AT-501"], ["Poolbau"], "sales.ops", 20, **kw))


def test_full_flow(store, tmp_path):
    loaded = _customers(store, tmp_path)
    assert loaded["test_records"] == 1

    ctx, summary = _capture(store)
    assert summary["found"] == 6
    evals = {r["business_id"]: r for r in store.select("rule_evaluation_out", {"run_id": ctx.run_id})}
    stages = sorted((r["stage"] or "INCLUDE") for r in evals.values())
    assert stages == ["CATEGORY_RULE", "CLOSED", "INCLUDE", "INCLUDE", "INCLUDE", "TITLE_RULE"]
    assert summary["excluded_as_customer"] == 1           # phone of customer C100 (hard signal)
    assert summary["pending_enrichment"] == 2
    places = {r["place_id"]: r for r in store.select("place_norm_out")}
    assert places["p_huber"]["subregion_code"] == "501" and places["p_huber"]["phone_e164"] == "+43662123456"

    prov = FakeProvider()
    ectx, es = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "sales.ops"))
    assert ectx.state == "CLOSED", ectx.log
    assert len(prov.sent) == 1                      # Mayr Bau has no website and no candidates: REVIEW, no model
    huber = next(r for r in store.select("web_validation_out") if r["domain"] == "poolbau-huber.at")
    assert huber["validation"] == "VALID" and huber["vat_id"] == "ATU12345678" and huber["has_ecommerce"] == 1
    scores = {r["business_id"]: r for r in store.select("score_out", {"run_id": ectx.run_id})}
    hs = scores[huber["business_id"]]
    # type 10, brands G1 7.5, zone ATTACK 7.5, e-commerce 5, person+email+phone 5, 40 reviews > P75 provisional 2.5
    assert list(hs["breakdown"]) == [10, 7.5, 7.5, 5, 5, 2.5] and hs["total"] == 37.5 and hs["rating"] == "HOT"
    mayr = next(r for r in store.select("web_validation_out") if r["business_id"] != huber["business_id"])
    assert (mayr["validation"], mayr["review_reason"]) == ("REVIEW", "NO_WEBSITE")

    batch = ctx.capture_batch_id
    pending = to_review(store, batch)
    assert set(pending) == set(scores)
    reject(store, batch, mayr["business_id"], "NOT_VALID", "sales.ops")
    assert approve_rest(store, batch, "sales.ops") == 1
    vals = current_validations(store, "AT", "POOL_SPECIALIST")
    assert vals[huber["business_id"]]["decision"] == "ACCEPTED"
    assert vals[huber["business_id"]]["approval_scope"] == "REST_OF_BATCH"

    xctx, xs = run_export(store, batch, "sales.ops", tmp_path / "exports")
    assert xs["exported"] == 1 and Path(xs["file"]).exists()
    from openpyxl import load_workbook
    ws = load_workbook(xs["file"]).active
    header = [c.value for c in ws[1]]
    row = dict(zip(header, [c.value for c in ws[2]]))
    assert row["Lead Source"] == "Web Scraping" and row["Rating"] == "Hot" and row["Owner Last Name"] == "Owner"
    assert row["Lead code"] == f"AT-{huber['business_id']}" and row["Currency"] == "EUR"
    assert row["E-Commerce"] == "True" and row["Category of Interest"] == "Pool Products & Services"
    assert huber["business_id"] not in published_ids(store, "AT")
    assert run_export(store, batch, "sales.ops", tmp_path / "exports")[1]["exported"] == 0     # exported once
    confirm(store, xs["export_id"], "UPLOADED", "sales.ops")
    assert huber["business_id"] in published_ids(store, "AT")

    # the human exclusion is remembered: the next capture discards Mayr Bau before anything is spent
    ctx2, _ = _capture(store)
    ev2 = {r["business_id"]: r for r in store.select("rule_evaluation_out", {"run_id": ctx2.run_id})}
    assert ev2[mayr["business_id"]]["stage"] == "PREVIOUSLY_EXCLUDED"

    # call results: the cockpit says Huber is already a customer
    from leadgen.loaders import load_cockpit
    ck = tmp_path / "cockpit.csv"
    ck.write_text(f"ID,Status,Not-qualified reason,Fluidra customer code,Attempts,Potential €/year (entered),"
                  f"Indicative potential €/year (from questions),Contact,Role,Phone,Email,Last call,Last comment\n"
                  f"AT-{huber['business_id']},Not qualified,Already Fluidra client,C555,2,,,,,,,2026-11-05,known\n"
                  f"AT-NL-002,New,,,0,,,,,,,,\n", encoding="utf-8")
    cfg = RunContext(store, "t", "LOAD", "AT", "POOL_SPECIALIST", "t").config()
    res = load_cockpit(store, cfg, ck, "AT", "2026-11-06", "sales.ops")
    assert res["unresolved_lead_codes"] == 1
    assert apply_cockpit_outcomes(store, "AT") == 1 and apply_cockpit_outcomes(store, "AT") == 0   # idempotent
    m = [r for r in store.select("customer_match_out") if r["origin"] == "COCKPIT"]
    assert m[0]["customer_code"] == "C555"


def test_rematch_disqualifies_unpublished_leads(store, tmp_path):
    ctx, _ = _capture(store, ITEMS[:1])
    run_enrich(store, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    approve_rest(store, ctx.capture_batch_id, "ops")
    f = tmp_path / "c.csv"
    f.write_text("customer_number,customer_name_original,Address,postal_code_normalized,city_normalized,country_code,"
                 "phone_normalized,vat_id_normalized,email_normalized,corporate_email_domain,Latitude,Longitude\n"
                 "C1,Huber,Weg,5020,Salzburg,AT,,ATU12345678,,,,\n", encoding="utf-8")
    cfg = RunContext(store, "t", "LOAD", "AT", "POOL_SPECIALIST", "t").config()
    load_customers(store, cfg, f, "AT", "TWP_SHEET_AT", "AT", store.now().date().isoformat(), "ops")
    _, s = run_rematch(store, "AT", "ops")
    assert s["new_customers"] == 1                         # the VAT from the website now matches
    _, x = run_export(store, ctx.capture_batch_id, "ops", tmp_path)
    assert x["exported"] == 0                              # disqualified: not exported


def test_batch_mode_and_resume(store):
    ctx, _ = _capture(store, ITEMS[:1])
    prov = FakeProvider()
    ectx, s = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops", llm_mode="BATCH"))
    assert ectx.state == "AWAITING_BATCHES"
    rows = store.select("llm_batch", {"run_id": ectx.run_id})
    assert [r["status"] for r in rows] == ["PREPARING", "SUBMITTED"]     # write-ahead before the call
    ectx2, s2 = resume_enrich(store, prov, crawler(), ectx.run_id)
    assert ectx2.state == "CLOSED" and s2["scored"] == 1


def test_invalid_answer_is_retried_once_then_technical_review(store):
    ctx, _ = _capture(store, ITEMS[:1])
    bad = FakeProvider(default={"validation": "VALID"})                      # schema violation
    ectx, s = run_enrich(store, bad, crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    assert len(bad.sent) == 2
    wv = store.select("web_validation_out", {"run_id": ectx.run_id})[0]
    assert (wv["validation"], wv["review_reason"]) == ("REVIEW", "TECHNICAL")


def test_cached_answer_is_not_paid_twice(store):
    ctx, _ = _capture(store, ITEMS[:1])
    prov = FakeProvider()
    run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops", reread=True))
    assert len(prov.sent) == 1


def test_cost_above_threshold_waits_for_approval(store):
    from leadgen.seed_loader import typed_rows, load
    load(store, {"biz_cfg_run_parameter": typed_rows("biz_cfg_run_parameter", [
        {"parameter": "cost_approval_threshold_usd", "value": "0.0001", "value_type": "FLOAT"}])}, "t", "t", "t",
         overwrite=True)
    ctx, _ = _capture(store, ITEMS[:1], approve_cost_usd=1)          # the capture is above the threshold too
    prov = FakeProvider()
    ectx, s = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    assert ectx.state == "AWAITING_COST_APPROVAL" and not prov.sent
    assert resume_enrich(store, prov, crawler(), ectx.run_id)[1]["message"] == "no COST approval yet"
    store.insert("biz_run_approval", [{"run_id": ectx.run_id, "approval_type": "COST", "approved_amount_usd": 5,
                                       "approved_by": "boss"}])
    ectx2, _ = resume_enrich(store, prov, crawler(), ectx.run_id)
    assert ectx2.state == "CLOSED" and len(prov.sent) == 1


def test_overlapping_area_is_rejected_while_running(store):
    from leadgen.state import RunContext as R
    other = R(store, "run_busy", "CAPTURE", "AT", "POOL_SPECIALIST", "x")
    other.set_state("REQUESTED", request={"area_ids": ["AT-S-SALZBURG"]})
    with pytest.raises(RunFailed, match="overlaps"):
        _capture(store)


def test_stale_extract_blocks(store, tmp_path):
    _customers(store, tmp_path)
    store.advance(days=40)
    with pytest.raises(RunFailed, match="days old"):
        _capture(store)


def test_profile_without_rules_cannot_enrich(store):
    with pytest.raises(prompt.ProfileNotConfigured):
        prompt.system_template("AT", "HVAC")


def test_reprocess_rules_is_free_and_rescored(store):
    ctx, _ = _capture(store, ITEMS[:1])
    prov = FakeProvider()
    run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    rctx, s = maintenance.run_reprocess(store, "AT", "POOL_SPECIALIST", "ops")
    assert rctx.state == "CLOSED" and s["scored"] == 1 and len(prov.sent) == 1


def test_every_table_written_exists_in_the_contract(store):
    assert set(store.tables) <= set(ddl.ALL_TABLES)


def _mayr_and_huber(store):
    """Huber (website, model) and Mayr Bau (no website, no candidates: REVIEW without the model)."""
    ctx, _ = _capture(store, [ITEMS[0], ITEMS[3]])
    return ctx, run_enrich(store, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))


def test_review_without_model_is_scored_and_reviewable(store):
    ctx, (ectx, s) = _mayr_and_huber(store)
    mayr = next(r["business_id"] for r in store.select("web_validation_out") if r["review_reason"] == "NO_WEBSITE")
    assert mayr in {r["business_id"] for r in store.select("score_out", {"run_id": ectx.run_id})}
    assert mayr in to_review(store, ctx.capture_batch_id)
    assert s["enriched"] == 2 and s["review"] == 1


def test_review_without_model_is_scored_in_batch_mode(store):
    ctx, _ = _capture(store, [ITEMS[0], ITEMS[3]])
    prov = FakeProvider()
    ectx, _ = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops", llm_mode="BATCH"))
    assert ectx.state == "AWAITING_BATCHES"
    _, s = resume_enrich(store, prov, crawler(), ectx.run_id)
    assert s["scored"] == 2


def test_stale_extract_can_be_approved_with_the_request(store, tmp_path):
    _customers(store, tmp_path)
    store.advance(days=40)
    ctx, _ = run_capture(store, FakeCapturer(ITEMS[:1]),
                         CaptureRequest("AT", "POOL_SPECIALIST", ["AT-501"], ["Poolbau"], "boss", 20,
                                        approve_stale_extract=True))
    assert ctx.state == "CLOSED"
    appr = store.select("biz_run_approval", {"run_id": ctx.run_id})
    assert [(a["approval_type"], a["approved_by"]) for a in appr] == [("STALE_CUSTOMER_EXTRACT", "boss")]


def _exported(store, tmp_path):
    ctx, _ = _capture(store, ITEMS[:1])
    run_enrich(store, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    approve_rest(store, ctx.capture_batch_id, "ops")
    _, xs = run_export(store, ctx.capture_batch_id, "ops", tmp_path)
    assert xs["exported"] == 1
    bid = store.select("export_lead_out", {"export_id": xs["export_id"]})[0]["business_id"]
    return ctx.capture_batch_id, xs["export_id"], bid


def test_failed_export_lead_can_be_reopened(store, tmp_path):
    batch, export_id, bid = _exported(store, tmp_path)
    with pytest.raises(ValueError, match="confirm it FAILED"):
        maintenance.reopen(store, batch, bid, "ops")
    store.advance(seconds=1)
    confirm(store, export_id, "FAILED", "ops")
    store.advance(seconds=1)
    maintenance.reopen(store, batch, bid, "ops")
    assert current_validations(store, "AT", "POOL_SPECIALIST")[bid]["decision"] == "PENDING"


@pytest.mark.parametrize("first,second", [("UPLOADED", "FAILED"), ("FAILED", "UPLOADED")])
def test_export_is_confirmed_only_once(store, tmp_path, first, second):
    _, export_id, bid = _exported(store, tmp_path)
    confirm(store, export_id, first, "ops")
    store.advance(seconds=1)
    with pytest.raises(ValueError, match="already"):
        confirm(store, export_id, second, "ops")
    assert (bid in published_ids(store, "AT")) == (first == "UPLOADED")


def _ledger(store, run_id, kind, provider="LLM"):
    return [r["amount_usd"] for r in store.select("cost_ledger", {"run_id": run_id, "provider": provider})
            if r["kind"] == kind]


def test_actual_llm_cost_counts_only_paid_calls_at_their_price(store):
    ctx, _ = _capture(store, ITEMS[:1])
    prov = FakeProvider()                                     # 1000 input / 300 output tokens per call
    e1, _ = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    assert _ledger(store, e1.run_id, "ACTUAL") == [0.01]      # (1000 x 4 + 300 x 20) / 1e6, DIRECT
    e2, s2 = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops", reread=True))
    assert len(prov.sent) == 1 and _ledger(store, e2.run_id, "ACTUAL") == [0.0]   # cached: nothing paid


def test_actual_llm_cost_applies_the_batch_discount(store):
    ctx, _ = _capture(store, ITEMS[:1])
    prov = FakeProvider()
    e, _ = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops", llm_mode="BATCH"))
    resume_enrich(store, prov, crawler(), e.run_id)
    assert _ledger(store, e.run_id, "ACTUAL") == [0.005]


def test_actual_llm_cost_includes_retries(store):
    ctx, _ = _capture(store, ITEMS[:1])
    bad = FakeProvider(default={"validation": "VALID"})
    e, _ = run_enrich(store, bad, crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    assert len(bad.sent) == 2 and _ledger(store, e.run_id, "ACTUAL") == [0.02]


def test_failed_rerun_does_not_hide_a_valid_cached_answer(store, monkeypatch):
    from . import conftest
    ctx, _ = _capture(store, ITEMS[:1])
    run_enrich(store, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    # the website changes and the new answer fails twice (first attempt + retry)
    imp = "https://poolbau-huber.at/impressum"
    monkeypatch.setitem(conftest.SITES, imp, conftest.SITES[imp].replace("Max Huber", "Eva Huber"))
    store.advance(days=181)                                   # past web_cache_ttl_days: crawled again
    bad = FakeProvider(default={"validation": "VALID"})
    run_enrich(store, bad, crawler(), EnrichRequest(ctx.capture_batch_id, "ops", reread=True))
    assert len(bad.sent) == 2
    monkeypatch.undo()
    store.advance(days=181)
    # back to the original content: the latest answer for this custom_id is the failed one, so it is asked again
    prov = FakeProvider()
    e3, _ = run_enrich(store, prov, crawler(), EnrichRequest(ctx.capture_batch_id, "ops", reread=True))
    wv = store.select("web_validation_out", {"run_id": e3.run_id})[0]
    assert len(prov.sent) == 1 and wv["validation"] == "VALID"


def test_capture_and_enrich_estimate_the_same(store):
    ctx, s = _capture(store)
    e, _ = run_enrich(store, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    assert s["pending_enrichment"] == 3
    assert _ledger(store, ctx.run_id, "ESTIMATED") == _ledger(store, e.run_id, "ESTIMATED")


def test_rematch_covers_every_profile(store, tmp_path):
    ctx, _ = _capture(store, ITEMS[:1])
    run_enrich(store, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    # the same business is a lead in another profile and is no longer included in POOL_SPECIALIST
    ev = store.select("rule_evaluation_out", {"run_id": ctx.run_id})[0]
    base = {k: v for k, v in ev.items() if k != "inserted_at"}
    store.insert("rule_evaluation_out", [{**base, "profile": "HVAC"}])
    store.advance(seconds=1)
    store.insert("rule_evaluation_out", [{**base, "decision": "EXCLUDE"}])
    f = tmp_path / "c.csv"
    f.write_text("customer_number,customer_name_original,Address,postal_code_normalized,city_normalized,country_code,"
                 "phone_normalized,vat_id_normalized,email_normalized,corporate_email_domain,Latitude,Longitude\n"
                 "C1,Huber,Weg,5020,Salzburg,AT,,ATU12345678,,,,\n", encoding="utf-8")
    cfg = RunContext(store, "t", "LOAD", "AT", "POOL_SPECIALIST", "t").config()
    load_customers(store, cfg, f, "AT", "TWP_SHEET_AT", "AT", store.now().date().isoformat(), "ops")
    _, s = run_rematch(store, "AT", "ops")
    assert s["new_customers"] == 1


def _threshold(store, usd):
    from leadgen.seed_loader import typed_rows, load
    load(store, {"biz_cfg_run_parameter": typed_rows("biz_cfg_run_parameter", [
        {"parameter": "cost_approval_threshold_usd", "value": str(usd), "value_type": "FLOAT"}])}, "t", "t", "t",
         overwrite=True)


def test_capture_estimates_apify_before_calling(store):
    ctx, _ = _capture(store, ITEMS[:1])
    assert _ledger(store, ctx.run_id, "ESTIMATED", "APIFY") == [0.14]        # 1 query x 20 places x 0.007


def test_capture_sends_the_area_outline_not_its_name(store):
    cap = FakeCapturer(ITEMS[:1])
    run_capture(store, cap, CaptureRequest("AT", "POOL_SPECIALIST", ["AT-501", "AT-S-SALZBURG"], ["Poolbau"],
                                           "ops", 20))
    district, state = cap.calls
    for sent in (district, state):        # by name alone Apify returns nothing (exploration/findings.md)
        assert (sent["countryCode"], sent["state"], sent["county"], sent["locationQuery"]) == ("", "", "", "")
    square = [[list(p) for p in SQUARE["coordinates"][0]]]
    assert district["customGeolocation"] == {"type": "MultiPolygon", "coordinates": [square]}
    assert state["customGeolocation"] == district["customGeolocation"]      # a state is its districts


def test_capture_without_boundary_fails_before_spending(store):
    rows = _cfg_rows()["biz_cfg_search_area"] + [
        {"country": "AT", "area_id": "AT-502", "parent_area_id": "AT-S-SALZBURG", "level": "SUBREGION",
         "admin_code": "502", "area_name": "Hallein", "default_language": "de",
         "actor_fields": json.dumps({"countryCode": "AT", "state": "Salzburg", "county": "Hallein"})}]
    typed = seed_loader.typed_rows("biz_cfg_search_area", [{k: (str(v) if v is not None else None)
                                                            for k, v in r.items()} for r in rows])
    seed_loader.load(store, {"biz_cfg_search_area": typed}, "test-502", "test", "synthetic", overwrite=True)
    store.advance(seconds=1)
    cap = FakeCapturer(ITEMS[:1])
    with pytest.raises(RunFailed, match="no boundary for areas AT-502"):
        run_capture(store, cap, CaptureRequest("AT", "POOL_SPECIALIST", ["AT-502"], ["Poolbau"], "ops", 20))
    assert not cap.calls


def test_capture_above_threshold_needs_approval_with_the_request(store):
    _threshold(store, 0.1)
    cap = FakeCapturer(ITEMS[:1])
    with pytest.raises(RunFailed, match="--approve-cost 0.14"):
        run_capture(store, cap, CaptureRequest("AT", "POOL_SPECIALIST", ["AT-501"], ["Poolbau"], "ops", 20))
    assert not cap.calls                                                     # nothing spent
    with pytest.raises(RunFailed, match="above the approval threshold"):    # approving less is not enough
        _capture(store, ITEMS[:1], approve_cost_usd=0.10)
    ctx, _ = _capture(store, ITEMS[:1], approve_cost_usd=0.14)
    assert ctx.state == "CLOSED"
    appr = store.select("biz_run_approval", {"run_id": ctx.run_id})
    assert [(a["approval_type"], a["approved_by"]) for a in appr] == [("COST", "sales.ops")]


def test_reject_reason_must_apply_to_the_profile(store):
    ctx, _ = _capture(store, ITEMS[:1])
    run_enrich(store, FakeProvider(), crawler(), EnrichRequest(ctx.capture_batch_id, "ops"))
    bid = to_review(store, ctx.capture_batch_id)[0]
    store.advance(seconds=1)                                                 # NOT_VALID now only for HVAC
    store.insert("biz_cfg_validation_reason", [{"reason_code": "NOT_VALID", "label": "x", "requires_customer_code": 0,
                                                "requires_duplicate_of": 0, "applies_to_profiles": ["HVAC"],
                                                "active": 1, "updated_by": "t"}])
    with pytest.raises(ValueError, match="does not apply to profile POOL_SPECIALIST"):
        reject(store, ctx.capture_batch_id, bid, "NOT_VALID", "ops")
    with pytest.raises(ValueError, match="unknown"):
        reject(store, ctx.capture_batch_id, bid, "NO_SUCH_REASON", "ops")
    reject(store, ctx.capture_batch_id, bid, "KA_CHAIN", "ops")              # applies to every profile
