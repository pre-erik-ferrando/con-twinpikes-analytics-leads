"""Unit tests of the pure modules, written from the definition's own examples where it gives them."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from leadgen import cost, export, normalize as N, runs, scoring
from leadgen.config import Config, latest_by_key, precheck, scoped
from leadgen.content import Page, prepare
from leadgen.filtering import FilterInput, evaluate, title_matches
from leadgen.geo import AdminArea, outline_geojson, polygons_from_geojson
from leadgen.identity import Business, Listing, assign, pick_master, resolve_alias
from leadgen.matching import (Customer, CustomerIndex, LeadFacts, MatchConfig, apply_verdicts, match,
                              resolve_priority)
from leadgen.response import SchemaError, check_answer, check_schema

from .conftest import SQUARE, answer


# --------------------------------------------------------------------------- normalize (DT.4.1)
class TestNormalize:
    def test_phone_e164_with_country_default(self):
        assert N.normalize_phone("0664 75060082", "AT") == ("+4366475060082", "MOBILE")
        assert N.normalize_phone("+43 662 123456", "AT")[0] == "+43662123456"
        assert N.normalize_phone("No website", "AT") == (None, None)

    def test_domain(self):
        assert N.registrable_domain("https://www.Poolbau-Huber.at/kontakt?x=1#a") == "poolbau-huber.at"
        assert N.registrable_domain("http://shop.example.co.at") == "example.co.at"
        assert N.registrable_domain("https://huber.wixsite.com/pool", {"wixsite.com"}) == "huber.wixsite.com"
        assert N.registrable_domain("NO WEB") is None

    def test_name_keys_umlaut_both_ways_and_legal_forms(self):
        forms = [N.normalize_text(f) for f in ("gmbh und co kg", "gmbh", "kg", "s.r.l.", "srl")]
        ae, a = N.name_keys("Müller Poolbau GmbH & Co. KG", forms)
        assert ae == "mueller poolbau"
        assert a == "muller poolbau"
        assert N.name_keys("Mueller Poolbau", forms)[0] == ae          # either key matches
        assert N.name_keys("Piscine S.r.l.", forms)[0] == "piscine"

    def test_exact_key_ignores_conjunctions(self):
        assert N.exact_key(N.name_keys("Peeters en Zonen")[0]) == N.exact_key(N.name_keys("Peeters & Zonen")[0])

    def test_postal_code(self):
        assert N.normalize_postal_code("1010", "AT") == "1010"
        assert N.normalize_postal_code("8000.0", "CH") == "8000"
        assert N.normalize_postal_code("1067", "DE") == "01067"
        assert N.normalize_postal_code("XXX", "AT") is None
        assert N.normalize_postal_code("1234 ab", "NL") == "1234AB"
        assert N.normalize_postal_code("A-5020", "AT") == "5020"

    def test_vat(self):
        assert N.normalize_vat("ATU 12345678") == "ATU12345678"
        assert N.normalize_vat("CHE-123.456.789 MWST") == "CHE123456789"
        assert N.normalize_vat("DE123456789") == "DE123456789"
        assert N.normalize_vat("BE 0123.456.789") == "BE0123456789"
        assert N.normalize_vat("NL123456789B01") == "NL123456789B01"
        assert N.normalize_vat("12/345/67890") is None      # a Steuernummer is never VAT

    def test_register(self):
        assert N.normalize_register("HRB 12345", "HRB", "DE", "Amtsgericht München") == "HRB12345@muenchen"
        assert N.normalize_register("HRB 12345", "HRB", "DE") is None          # no court, no identity
        assert N.normalize_register("FN 123456a", "FN", "AT") == "FN123456A"
        assert N.normalize_register("0123.456.789", "KBO", "BE") == "KBO0123456789"
        assert N.normalize_register("12345678", "KVK", "NL") == "KVK12345678"


# --------------------------------------------------------------------------- geo
def test_point_in_polygon_and_bbox():
    area = AdminArea("AT", "SUBREGION", "501", "Salzburg", None, polygons_from_geojson(SQUARE))
    assert area.contains(13.05, 47.8)
    assert not area.contains(13.2, 47.8)


def test_outline_drops_needless_points_and_keeps_holes():
    # A ring around a hole (Graz-Umgebung around Graz), with extra points on straight edges.
    outer = [(0, 0), (1, 0), (2, 0), (2, 1), (2, 2), (1, 2), (0, 2), (0, 1), (0, 0)]
    hole = [(0.5, 0.5), (1.5, 0.5), (1.5, 1.5), (0.5, 1.5), (0.5, 0.5)]
    speck = [(5, 5), (5.00001, 5), (5.00001, 5.00001), (5, 5)]          # collapses at 100 m
    out = outline_geojson([[outer, hole], [speck]], 0.001)
    assert out["type"] == "MultiPolygon" and len(out["coordinates"]) == 1
    ring, inner = out["coordinates"][0]
    assert len(ring) == 5 and len(inner) == 5
    simplified = AdminArea("AT", "SUBREGION", "x", "x", None, polygons_from_geojson(out))
    assert simplified.contains(0.2, 0.2) and not simplified.contains(1, 1)    # the hole is still a hole


# --------------------------------------------------------------------------- identity (DT.4.2)
class TestIdentity:
    H = staticmethod(lambda pid: abs(hash(pid)) % 10**12)

    def test_new_listing_gets_hash_of_its_place_id(self):
        res = assign([Listing("p1", "poolbau huber", "+43662123456", 47.8, 13.0)], [], {}, self.H)
        assert res.links[0]["business_id"] == self.H("p1")

    def test_same_phone_and_similar_name_inherits(self):
        b = Business(1, "2026-01-01", ["p1"], {"+43662123456"}, {"poolbau huber"}, [(47.8, 13.0)])
        res = assign([Listing("p2", "huber pools", "+43662123456", 48.5, 14.0)], [b], {"p1": 1}, self.H)
        assert res.links[0]["business_id"] == 1

    def test_same_phone_unrelated_name_far_away_is_suspect(self):
        b = Business(1, "2026-01-01", ["p1"], {"+43662123456"}, {"poolbau huber"}, [(47.8, 13.0)])
        res = assign([Listing("p2", "zahnarzt dr mayr", "+43662123456", 48.5, 14.0)], [b], {"p1": 1}, self.H)
        assert res.links[0]["business_id"] != 1 and res.merge_suspects

    def test_listing_joining_two_businesses_keeps_the_oldest(self):
        old = Business(1, "2026-01-01", ["p1"], {"+431"}, {"pool a"}, [])
        new = Business(2, "2026-02-01", ["p2"], {"+431"}, {"pool a"}, [])
        res = assign([Listing("p3", "pool a", "+431", None, None)], [old, new], {"p1": 1, "p2": 2}, self.H)
        assert res.links[0]["business_id"] == 1
        assert res.aliases == [{"old_id": 2, "surviving_id": 1, "reason": "LISTING_JOINS_BUSINESSES", "linked_at": ""}]
        assert resolve_alias(2, {2: 1}) == 1

    def test_master_listing(self):
        a = {"place_id": "a", "name": "A", "website": None, "reviews_count": 50}
        b = {"place_id": "b", "name": "B", "website": "x.at", "reviews_count": 3}
        assert pick_master([a, b])["place_id"] == "b"


# --------------------------------------------------------------------------- config (DT.2)
class TestConfig:
    def test_argmax_and_soft_delete(self):
        t0 = datetime(2026, 1, 1)
        rows = [{"country": None, "rating": "HOT", "min_score": 30, "active": 1, "updated_by": "seed", "inserted_at": t0},
                {"country": None, "rating": "HOT", "min_score": 28, "active": 1, "updated_by": "ana",
                 "inserted_at": t0 + timedelta(1)},
                {"country": None, "rating": "WARM", "min_score": 20, "active": 1, "updated_by": "s", "inserted_at": t0},
                {"country": None, "rating": "WARM", "min_score": 20, "active": 0, "updated_by": "s",
                 "inserted_at": t0 + timedelta(1)}]
        cur = latest_by_key(rows, ("country", "rating"))
        assert [(r["rating"], r["min_score"]) for r in cur] == [("HOT", 28)]
        assert latest_by_key(rows, ("country", "rating"), snapshot_at=t0)[0]["min_score"] == 30

    def test_country_and_profile_rows_win_over_null(self):
        rows = [{"country": None, "profile": None, "parameter": "x", "value": "1"},
                {"country": "AT", "profile": None, "parameter": "x", "value": "2"},
                {"country": "DE", "profile": None, "parameter": "x", "value": "3"}]
        assert scoped(rows, ("country", "parameter"), "AT")[0]["value"] == "2"
        assert scoped(rows, ("country", "parameter"), "CH")[0]["value"] == "1"

    def test_precheck_blocks_missing_rules(self, store):
        from leadgen.state import RunContext
        cfg = RunContext(store, "t", "CAPTURE", "AT", "POOL_SPECIALIST", "t").config()
        assert precheck(cfg, ["AT-501"], ["Poolbau"]).blocking == []
        cfg_hvac = RunContext(store, "t", "CAPTURE", "AT", "HVAC", "t").config()
        assert any("not active" in b for b in precheck(cfg_hvac, ["AT-501"], ["x"]).blocking)
        assert any("not active in biz_cfg_search_area" in b for b in precheck(cfg, ["AT-999"], ["x"]).blocking)


# --------------------------------------------------------------------------- filter (DT.4.4)
RULES = {"schwimmbadbauer": "INCLUDE", "bauunternehmen": "CONDITIONAL", "hotel": "EXCLUDE", "baumarkt": "EXCLUDE",
         "ofen": "EXCLUDE"}
TITLES = [{"pattern": "Hornbach", "pattern_normalized": "hornbach", "match_type": "TOKEN", "rule_class": "HARD",
           "reason_code": "KA_CHAIN"},
          {"pattern": "Sauna", "pattern_normalized": "sauna", "match_type": "TOKEN", "rule_class": "SOFT",
           "reason_code": "OUT_OF_PROFILE"}]
STEMS = ["pool", "schwimmbad", "schwimmteich"]


class TestFilter:
    def ev(self, name, cats, **kw):
        return evaluate(FilterInput(1, name, cats, **kw), RULES, TITLES, STEMS)

    def test_order_of_evaluation(self):
        assert self.ev("Poolbau", ["Schwimmbadbauer"], is_closed_permanently=True).stage == "CLOSED"
        assert self.ev("Poolbau", ["Schwimmbadbauer"], previous_exclusion="HUMAN:KA_CHAIN").stage == "PREVIOUSLY_EXCLUDED"
        d = self.ev("Hornbach Baumarkt", ["Schwimmbadbauer"])
        assert (d.stage, d.rule) == ("TITLE_RULE", "KA_CHAIN")            # HARD title beats INCLUDE

    def test_include_beats_category_exclusion(self):
        assert self.ev("Huber", ["Ofen", "Schwimmbadbauer"]).decision == "INCLUDE"

    def test_conditional_needs_pool_signal(self):
        assert self.ev("Huber Bau", ["Bauunternehmen"]).decision == "EXCLUDE"
        assert self.ev("Huber Bau", ["Bauunternehmen"], owner_description="Wir bauen Pools").decision == "INCLUDE"
        assert self.ev("Huber Schwimmteiche", ["Bauunternehmen"]).decision == "INCLUDE"

    def test_soft_then_category_then_default(self):
        assert self.ev("Sauna Welt", ["Bauunternehmen"]).stage == "TITLE_RULE"
        assert self.ev("Hotel Post", ["Hotel"]).stage == "CATEGORY_RULE"
        d = self.ev("Nails Studio", ["Nagelstudio"])
        assert d.stage == "DEFAULT_EXCLUDE" and d.unknown_categories == ["nagelstudio"]

    def test_hvac_needs_no_pool_signal(self):
        d = evaluate(FilterInput(1, "Huber Heizung", ["Bauunternehmen"]), RULES, TITLES, STEMS, "HVAC")
        assert d.decision == "INCLUDE"

    def test_title_match_types(self):
        assert title_matches("obi baumarkt", "obi", "TOKEN") and not title_matches("obiwan", "obi", "TOKEN")
        assert title_matches("hornbachmarkt", "hornbach", "PREFIX")
        assert title_matches("superhornbach", "hornbach", "SUBSTRING")


# --------------------------------------------------------------------------- matching (DT.5)
FORMS = ["gmbh", "ag", "kg"]


def _cust(code, name, **kw):
    ae, a = N.name_keys(name, FORMS)
    return Customer(kw.pop("sub", "AT"), code, name, kw.pop("country", "AT"), ae, a, **kw)


class TestMatching:
    MC = MatchConfig(shared_domains={"gmail.com"}, stopwords={"pool", "poolbau", "gmbh", "schwimmbad"})

    def lead(self, name, **kw):
        ae, a = N.name_keys(name, FORMS)
        return LeadFacts(1, "AT", ae, a, **kw)

    def test_hard_signal_vat(self):
        idx = CustomerIndex([_cust("12345", "Poolbau Beispiel GmbH", vat_id="ATU12345678")])
        r = match(self.lead("Irgendwas", vat_ids={"ATU12345678"}), idx, self.MC)
        assert (r.outcome, r.signal) == ("CUSTOMER", "VAT")
        assert r.reason_text == "VAT matches customer 12345 (subsidiary AT), Poolbau Beispiel GmbH"

    def test_shared_domain_never_matches(self):
        idx = CustomerIndex([_cust("1", "X", corporate_email_domain="gmail.com")])
        assert match(self.lead("Y", domains={"gmail.com"}), idx, self.MC).outcome == "NO_MATCH"

    def test_other_country_address_is_ignored(self):
        idx = CustomerIndex([_cust("1", "Huber Pool", phone_e164="+43662", country="DE")])
        assert match(self.lead("Huber Pool", phones={"+43662"}), idx, self.MC).outcome == "NO_MATCH"

    def test_exact_name_and_same_city_confirms_without_model(self):
        idx = CustomerIndex([_cust("7", "Huber Pools GmbH", city_norm="salzburg")])
        r = match(self.lead("Huber Pools", city_norm="salzburg"), idx, self.MC)
        assert (r.outcome, r.signal) == ("CUSTOMER", "NAME_CONFIRM")

    def test_fuzzy_needs_distinctive_word_and_goes_to_model(self):
        # word order differs (token_sort_ratio 100) but the city does not: the model decides
        idx = CustomerIndex([_cust("7", "Huber Poolbau", city_norm="wien"), _cust("8", "Mayr Poolbau")])
        r = match(self.lead("Poolbau Huber", city_norm="salzburg"), idx, self.MC)
        assert r.outcome == "CANDIDATES" and [c.customer.customer_code for c in r.candidates] == ["7"]
        assert apply_verdicts(r, {"C1": "DIFFERENT"}).outcome == "NO_MATCH"
        assert apply_verdicts(r, {}).outcome == "CUSTOMER"                 # missing verdict = UNSURE = customer

    def test_no_extract(self):
        assert match(self.lead("x"), None, self.MC).outcome == "NO_EXTRACT"

    def test_priority_human_over_hard(self):
        rows = [{"origin": "AUTO", "signal": "VAT", "outcome": "CUSTOMER", "inserted_at": 2},
                {"origin": "HUMAN", "signal": "HUMAN", "outcome": "CUSTOMER", "inserted_at": 1}]
        assert resolve_priority(rows)["origin"] == "HUMAN"


# --------------------------------------------------------------------------- scoring (DF.8, DT.7)
POINTS = {("COMPANY_TYPE", "BUILDER_INSTALLER_SHOP"): 10, ("COMPANY_TYPE", "MAINTENANCE_OR_PREFAB"): 5,
          ("COMPANY_TYPE", "WEAK_UNVERIFIED"): 2.5, ("BRANDS", "GROUP_1"): 7.5, ("BRANDS", "GROUP_2"): 5,
          ("BRANDS", "GROUP_3"): 2.5, ("BRANDS", "NONE"): 0, ("ZONE", "ATTACK"): 7.5, ("ZONE", "ACTIVATE"): 5,
          ("ZONE", "DEFEND"): 2.5, ("ZONE", "OPTIMIZE"): 1, ("ZONE", "NONE"): 0, ("DIGITAL", "ECOMMERCE"): 5,
          ("DIGITAL", "OWN_WEBSITE"): 2.5, ("DIGITAL", "NONE"): 0, ("CONTACT", "PERSON_EMAIL_PHONE"): 5,
          ("CONTACT", "EMAIL_PHONE"): 3, ("CONTACT", "ANY"): 1, ("CONTACT", "NONE"): 0,
          ("REVIEWS", "ABOVE_P75"): 2.5, ("REVIEWS", "ABOVE_MEDIAN"): 1.5, ("REVIEWS", "ANY"): 0.5,
          ("REVIEWS", "NONE"): 0}
BANDS = [("HOT", 30), ("WARM", 20), ("COLD", 0)]
REF = scoring.ReviewReference("AT-x", 34.8, 10.5, 68, False)


def _inp(**kw):
    base = dict(business_id=1, name="x", validation="VALID", company_type="BUILDER_INSTALLER_SHOP",
                competitor_brands=["SPECK"], subregion_code="501", has_ecommerce=False, own_domain="x.at",
                crawl_status="OK", contact_name="Max", email="a@x.at", phone_web="+43", phone_google=None,
                reviews_count=5)
    base.update(kw)
    return scoring.ScoreInput(**base)


class TestScoring:
    def test_maximum_is_37_5(self):
        assert sum(max(v for (f, _), v in POINTS.items() if f == factor) for factor in scoring.SCORING_FACTORS) == 37.5

    def test_cockpit_example_10_7_5_7_5_2_5_5_0_5_is_33(self):
        s = scoring.score(_inp(), POINTS, BANDS, {"SPECK": 1}, {"501": "ATTACK"}, REF)
        assert s.breakdown == [10, 7.5, 7.5, 2.5, 5, 0.5] and s.total == 33 and s.rating == "HOT"

    def test_review_forces_weak_and_strict_review_cuts(self):
        s = scoring.score(_inp(validation="REVIEW", reviews_count=35), POINTS, BANDS, {}, {}, REF)
        assert s.breakdown[0] == 2.5 and s.breakdown[5] == 2.5
        assert scoring.bucket_reviews(34.8, REF) == "ABOVE_MEDIAN"                 # strictly greater than P75
        assert scoring.bucket_reviews(None, REF) == "NONE"

    def test_contact_and_digital(self):
        assert scoring.bucket_contact(_inp(contact_name=None)) == "EMAIL_PHONE"
        assert scoring.bucket_contact(_inp(contact_name=None, email=None, phone_web=None, phone_google="+43")) == "ANY"
        assert scoring.bucket_digital(_inp(crawl_status="UNREACHABLE")) == "OWN_WEBSITE"   # a dead site still counts
        assert scoring.bucket_digital(_inp(crawl_status="SOCIAL_ONLY")) == "NONE"

    def test_percentile_inc_matches_excel(self):
        assert scoring.percentile_inc([1, 2, 3, 4], 0.75) == 3.25
        assert scoring.percentile_inc([1, 2, 3, 4], 0.5) == 2.5

    def test_provisional_reference_below_min_n(self):
        ref = scoring.review_reference([1, 2, 3], "v", 30, 34.8, 10.5)
        assert ref.is_provisional and ref.p75 == 34.8

    def test_rating_thresholds(self):
        assert scoring.rate(30, BANDS) == "HOT" and scoring.rate(29.99, BANDS) == "WARM" and scoring.rate(0, BANDS) == "COLD"


# --------------------------------------------------------------------------- content and response (DT.6)
class TestContent:
    def test_prepare_orders_dedupes_and_extracts(self):
        pages = [Page("", "https://x.at/", 0, "<p>Menu</p><p>Home text about pools</p><a href='tel:+43662'>t</a>"),
                 Page("", "https://x.at/impressum", 1, "<p>Menu</p><p>UID: ATU12345678</p><script>bad()</script>")]
        c = prepare(pages, ["impressum"], [{"brand": "SPECK"}], 4000, 30000, 100000, 0.1, 3, 200)
        assert c.pages[0].url.endswith("impressum") and c.pages[0].page_id == "P1"
        assert "ATU12345678" in c.pages[0].signals["vat"]
        assert "bad()" not in c.pages[0].text
        assert "Menu" not in c.pages[1].text                     # kept once, where it first appears
        assert c.content_line == "complete"

    def test_truncation_line(self):
        pages = [Page("", f"https://x.at/{i}", 1, "<p>" + "word " * 3000 + f"{i}</p>") for i in range(10)]
        c = prepare(pages, [], [], 4000, 6000, 100000, 0.1, 3, 200)
        assert c.truncated and c.content_line.startswith("truncated (")


class TestResponse:
    PAGES = {"P1": ("https://x.at/impressum", "Poolbau Huber GmbH, Hauptstraße 5, 5020 Salzburg. UID: ATU 123 456 78 "
                                              "Tel 0662 123456 office@x.at Geschäftsführer Max Huber"),
             "P2": ("https://x.at/shop", "Speck pumps in our shop")}

    def test_schema_strict(self):
        check_schema(answer())
        with pytest.raises(SchemaError):
            check_schema({**answer(), "extra": 1})
        with pytest.raises(SchemaError):
            check_schema({**answer(), "validation": "MAYBE"})

    def test_claims_checked_against_cited_page(self):
        a = check_answer(answer(email="office@x.at", phone="0662 123456"), self.PAGES, "AT",
                         [{"brand": "SPECK"}], [{"brand": "ZODIAC"}], [], False)
        assert a.vat_id == "ATU12345678" and a.email == "office@x.at" and a.phone_e164 == "+43662123456"
        assert a.detected_brands == ["SPECK"] and a.has_ecommerce is True and a.n_unsupported_claims == 0

    def test_unsupported_claims_are_dropped(self):
        a = check_answer(answer(legal_name="Invented AG", contact_source="P9", detected_brands=[
            {"brand": "BAYROL", "source_page": "P2"}]), self.PAGES, "AT", [{"brand": "SPECK"}, {"brand": "BAYROL"}],
            [], [], False)
        assert a.legal_name is None and a.contact_name is None and a.detected_brands == []
        # invented name, contact on a page that does not exist, BAYROL not on P2, and the default
        # answer's email, which is not on P1 either
        assert a.n_unsupported_claims == 4 and a.email is None

    def test_verdicts_one_per_candidate(self):
        a = check_answer(answer(customer_checks=[
            {"candidate_ref": "C1", "verdict": "DIFFERENT", "reason": "x"},
            {"candidate_ref": "C2", "verdict": "SAME", "reason": "x"},
            {"candidate_ref": "C2", "verdict": "DIFFERENT", "reason": "x"},
            {"candidate_ref": "C7", "verdict": "SAME", "reason": "x"}]), self.PAGES, "AT", [], [], ["C1", "C2", "C3"],
            False)
        assert a.verdicts == {"C1": "DIFFERENT", "C2": "UNSURE", "C3": "UNSURE"} and a.dropped_refs == ["C7"]

    def test_review_forces_weak(self):
        a = check_answer(answer(validation="REVIEW", company_type="BUILDER_INSTALLER_SHOP"), self.PAGES, "AT", [], [],
                         [], False)
        assert a.company_type == "WEAK_UNVERIFIED" and a.offering is None


# --------------------------------------------------------------------------- runs, cost, export
def test_area_locking_same_parent_child():
    areas = {"AT-S": {}, "AT-501": {"parent_area_id": "AT-S"}, "AT-502": {"parent_area_id": "AT-S"}}
    assert runs.area_conflicts(["AT-501"], {"r1": ["AT-502"]}, areas) == []
    assert runs.area_conflicts(["AT-501"], {"r1": ["AT-S"]}, areas)
    assert runs.area_conflicts(["AT-S"], {"r1": ["AT-501"]}, areas)


def test_transitions():
    runs.check_transition(None, "REQUESTED")
    with pytest.raises(runs.TransitionError):
        runs.check_transition("CLOSED", "RUNNING")


def test_cost_approval():
    p = cost.Prices()
    assert cost.llm_cost(100, 30000, 1200, "BATCH", p) == pytest.approx(cost.llm_cost(100, 30000, 1200, "DIRECT", p) / 2,
                                                                         abs=0.01)
    assert not cost.needs_approval(5, 10, None) and cost.needs_approval(15, 10, None)
    assert not cost.needs_approval(15, 10, 20) and cost.overrun(25, 20, 10, 0.2)



def test_actual_cost_counts_cache_writes():
    p = cost.Prices()
    base = {"input_tokens": 1000, "cache_read_tokens": 3500, "output_tokens": 1200, "mode": "DIRECT"}
    first = {**base, "cache_read_tokens": 0, "cache_write_tokens": 3500}
    assert cost.llm_actual_cost([first], p) - cost.llm_actual_cost([{**first, "cache_write_tokens": 0}], p) \
        == pytest.approx(3500 * p.llm_cache_write_per_mtok / 1e6, abs=1e-4)
    assert cost.llm_actual_cost([first], p) > cost.llm_actual_cost([base], p)


def test_dotenv_drops_inline_comments(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("leadgen_cli", Path(__file__).resolve().parents[4] / "leadgen.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    env = tmp_path / ".env"
    env.write_text("A_X=fld_landinglgn   # landing schema\nB_X='a #b'  # quoted\nC_X=p#ss\nD_X=\n# E_X=1\n",
                   encoding="utf-8")
    for k in ("A_X", "B_X", "C_X", "D_X", "E_X"):
        monkeypatch.delenv(k, raising=False)
    cli.load_dotenv(env)
    import os
    assert (os.environ["A_X"], os.environ["B_X"], os.environ["C_X"], os.environ["D_X"]) == \
        ("fld_landinglgn", "a #b", "p#ss", "")
    assert "E_X" not in os.environ

def test_export_rows_from_config():
    cols = [{"column_name": "Lead Source", "source_kind": "CONSTANT", "source_value": "Web Scraping", "format": "TEXT"},
            {"column_name": "Rating", "source_kind": "VALUE_MAP", "source_value": "SF_RATING", "source_field": "rating",
             "format": "TEXT"},
            {"column_name": "Email", "source_kind": "FIELD", "source_value": "email", "default_value": None,
             "format": "TEXT"},
            {"column_name": "E-Commerce", "source_kind": "VALUE_MAP", "source_value": "SF_ECOMMERCE",
             "source_field": "has_ecommerce", "format": "BOOLEAN_TEXT"}]
    header, rows = export.build_rows([{"rating": "HOT", "email": None, "has_ecommerce": 1}], cols,
                                     {"SF_RATING": {"HOT": "Hot"}, "SF_ECOMMERCE": {"1": "True", "0": "False"}})
    assert header == ["Lead Source", "Rating", "Email", "E-Commerce"] and rows == [["Web Scraping", "Hot", None, "True"]]
    with pytest.raises(export.ExportError):
        export.build_rows([{"x": 1}], [cols[2]], {})
    assert export.lead_code("AT", 42) == "AT-42"


def test_config_from_rows_is_scoped_by_profile(store):
    from leadgen.state import RunContext
    cfg = RunContext(store, "t", "CAPTURE", "AT", "POOL_SPECIALIST", "t").config()
    assert isinstance(cfg, Config)
    assert ("COMPANY_TYPE", "BUILDER_INSTALLER_SHOP") in cfg.scoring_points()
    assert cfg.param("customer_extract_max_age_days") == 7
    assert cfg.sales_owner("999")["owner_last_name"] == "Owner"           # the country default owner


def test_inputs_excel_with_title_rows_as_in_v3(tmp_path):
    """TWP's v3 layout: title and description rows above the header, brand group as 'Group 1 (7.5)'."""
    from openpyxl import Workbook
    from leadgen.seed_loader import excel_to_rows
    tabs = {
        "Admin divisions": [["Country code", "Country", "Region", "Subregion code", "Subregion",
                             "Default language", "Zone"], ["AT", "Austria", "Steiermark", "601", "Graz(Stadt)", "de", "Attack"]],
        "Profiles & keywords": [["Profile", "Language", "Countries", "Keyword", "Pre-selected in Capture"],
                                ["Pool specialist", "de", "AT", "Poolbau", "Pre-selected"],
                                ["Pool specialist", "de", "AT", "Poolzubehör", "Optional"]],
        "Google categories": [["Category", "Rule"], ["Poolbedarf", "INCLUDE"]],
        "Name exclusions": [["Pattern", "Class", "Reason"], ["OBI", "HARD", "Key Account chain"]],
        "Pool words": [["Language", "Root"], ["de", "pool"]],
        "Brands": [["Brand", "Group"], ["BAYROL", "Group 1 (7.5)"], ["ZODIAC", "Fluidra (no score)"]],
        "Subregion → sales rep": [["Country code", "Subregion code", "Sales rep first name", "Sales rep last name"],
                                  ["AT", "601", "Pending Fluidra", None]],
    }
    wb = Workbook()
    wb.remove(wb.active)
    for name, rows in tabs.items():
        ws = wb.create_sheet(name)
        ws.append([name])
        ws.append(["One-cell description of the tab."])
        for r in rows:
            ws.append(r)
    wb.save(tmp_path / "inputs.xlsx")
    out = excel_to_rows(tmp_path / "inputs.xlsx")
    assert [a["area_id"] for a in out["biz_cfg_search_area"]] == ["AT-S-STEIERMARK", "AT-601"]
    assert out["biz_cfg_strategic_zone"][0]["quadrant"] == "ATTACK"
    assert [(t["term"], t["preselected"]) for t in out["biz_cfg_search_term"]] == [("Poolbau", "1"), ("Poolzubehör", "0")]
    assert out["biz_cfg_title_rule"][0]["reason_code"] == "KA_CHAIN"
    assert [(b["owner"], b["brand_group"]) for b in out["biz_cfg_brand"]] == [("COMPETITOR", "1"), ("FLUIDRA", None)]
    assert out["biz_cfg_sales_owner"] == []


def test_load_geo_keeps_only_twp_codes(tmp_path):
    """Vienna is 900 and also 901-923 in Statistik Austria's file; TWP uses 900 (DT.4.1)."""
    import json
    from leadgen import loaders
    from leadgen.store import MemoryStore
    feats = [{"type": "Feature", "properties": {"g_id": c, "g_name": n},
              "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}}
             for c, n in [("900", "Wien(Stadt)"), ("909", "Wien 9.,Alsergrund"), ("601", "Graz(Stadt)")]]
    path = tmp_path / "at.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": feats}))
    st = MemoryStore()
    r = loaders.load_geo(st, path, "AT", "SUBREGION", "TEST", "1", "g_id", "g_name", keep={"900", "601", "107"})
    assert r == {"loaded": 2, "skipped": ["909"], "missing": ["107"]}
    assert sorted(x["admin_code"] for x in st.select("reference_geo")) == ["601", "900"]
    assert loaders.load_geo(MemoryStore(), path, "AT", "SUBREGION", "TEST", "1", "g_id", "g_name")["loaded"] == 3
