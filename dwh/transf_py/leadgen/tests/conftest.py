"""Fixtures: a MemoryStore seeded like a real Austrian start (seeds + the Excel tabs we do not have,
replaced by a few synthetic rows), fake Apify, fake website and fake model."""
from __future__ import annotations

import json

import pytest

from leadgen import seed_loader
from leadgen.apify import ActorRun
from leadgen.crawl import Crawler, CrawlLimits, Response
from leadgen.llm import LLMResult
from leadgen.store import MemoryStore  # noqa: F401
from leadgen.tests.filter_reference import ReferenceStore

# A square "Bezirk" around Salzburg city: lon 13.0-13.1, lat 47.7-47.9.
SQUARE = {"type": "Polygon", "coordinates": [[[13.0, 47.7], [13.1, 47.7], [13.1, 47.9], [13.0, 47.9], [13.0, 47.7]]]}


def _cfg_rows():
    """The rows the inputs Excel would bring for one Austrian subregion."""
    return {
        "biz_cfg_search_area": [
            {"country": "AT", "area_id": "AT-S-SALZBURG", "level": "STATE", "admin_code": "AT-S-SALZBURG",
             "area_name": "Salzburg", "actor_fields": json.dumps({"countryCode": "AT", "state": "Salzburg"})},
            {"country": "AT", "area_id": "AT-501", "parent_area_id": "AT-S-SALZBURG", "level": "SUBREGION",
             "subregion_type": "BEZIRK", "admin_code": "501", "area_name": "Salzburg(Stadt)", "region_name": "Salzburg",
             "default_language": "de",
             "actor_fields": json.dumps({"countryCode": "AT", "state": "Salzburg", "county": "Salzburg(Stadt)"})},
        ],
        "biz_cfg_strategic_zone": [{"country": "AT", "admin_level": "SUBREGION", "admin_code": "501",
                                    "admin_name": "Salzburg(Stadt)", "quadrant": "ATTACK", "model_version": "test"}],
        "biz_cfg_category_rule": [
            {"country": None, "language": "de", "category": "Schwimmbadbauer", "classification": "INCLUDE"},
            {"country": None, "language": "de", "category": "Schwimmbadzubehörgeschäft", "classification": "INCLUDE"},
            {"country": None, "language": "de", "category": "Bauunternehmen", "classification": "CONDITIONAL"},
            {"country": None, "language": "de", "category": "Hotel", "classification": "EXCLUDE"},
            {"country": None, "language": "de", "category": "Baumarkt", "classification": "EXCLUDE"},
        ],
        "biz_cfg_brand": [
            {"country": None, "brand": "SPECK", "owner": "COMPETITOR", "brand_group": 1},
            {"country": None, "brand": "BAYROL", "owner": "COMPETITOR", "brand_group": 2},
        ],
        "biz_cfg_sales_owner": [{"country": "AT", "subregion_code": None, "owner_first_name": "Default",
                                 "owner_last_name": "Owner"}],
    }


@pytest.fixture
def store():
    st = ReferenceStore()       # the in-memory store, with the filter's Python reference (the engine's is SQL)
    seed_loader.load_csv_seeds(st, "test")
    typed = {t: seed_loader.typed_rows(t, [{k: (str(v) if v is not None else None) for k, v in r.items()}
                                           for r in rows]) for t, rows in _cfg_rows().items()}
    seed_loader.load(st, typed, "test-excel", "test", "synthetic")
    st.insert("reference_geo", [{"source": "TEST", "country": "AT", "version": "1", "admin_level": "SUBREGION",
                                 "admin_code": "501", "admin_name": "Salzburg(Stadt)",
                                 "geometry_json": json.dumps(SQUARE)}])
    st.advance(seconds=1)
    return st


def item(place_id, title, categories, website=None, phone=None, lat=47.8, lng=13.05, reviews=10, **kw):
    return {"placeId": place_id, "title": title, "categories": categories, "website": website,
            "phoneUnformatted": phone, "location": {"lat": lat, "lng": lng}, "reviewsCount": reviews,
            "totalScore": 4.6, "address": f"{title}str. 1, 5020 Salzburg", "city": "Salzburg", "postalCode": "5020",
            "permanentlyClosed": kw.get("closed", False), "temporarilyClosed": False,
            "description": kw.get("description")}


class FakeCapturer:
    build = "test-build"

    def __init__(self, items):
        self.items, self.calls = items, []

    def run(self, actor_in, max_charge_usd):
        self.calls.append(actor_in)
        return ActorRun(f"apify{len(self.calls)}", "SUCCEEDED", list(self.items), 0.25, None, None)


SITES = {
    "https://poolbau-huber.at": """<html><body><h1>Poolbau Huber</h1><p>Wir bauen Schwimmbäder und verkaufen
        Poolzubehör. Wir sind Partner von Speck Pumpen.</p><a href="/impressum">Impressum</a>
        <a href="/shop/warenkorb">Warenkorb</a></body></html>""",
    "https://poolbau-huber.at/impressum": """<html><body><p>Poolbau Huber GmbH, Hauptstraße 5, 5020 Salzburg</p>
        <p>Geschäftsführer: Max Huber</p><p>UID: ATU12345678</p><p>Tel: +43 662 123456</p>
        <p>office@poolbau-huber.at</p></body></html>""",
}


def fake_fetch(url, timeout):
    url = url.rstrip("/")
    if url.endswith("robots.txt"):
        return Response(url, 404, "text/plain", "")
    if url.endswith("sitemap.xml"):
        return Response(url, 404, "text/xml", "")
    if url in SITES:
        return Response(url, 200, "text/html", SITES[url])
    return Response(url, 404, "text/html", "")


def crawler():
    lim = CrawlLimits(requests_per_second=0, render_text_threshold=10)
    return Crawler(lim, ["impressum", "kontakt"], {"wixsite.com"}, fetch=fake_fetch, sleep=lambda s: None)


def answer(**over):
    base = {"validation": "VALID", "validation_reason": "Builds pools and sells equipment; buys Speck.",
            "offering": "BOTH", "pool_focus": "TOTAL", "company_type": "BUILDER_INSTALLER_SHOP",
            "legal_name": "Poolbau Huber GmbH", "legal_name_source": "P1",
            "legal_address": "Hauptstraße 5, 5020 Salzburg", "legal_address_source": "P1",
            "vat_id": "ATU12345678", "vat_id_source": "P1", "register_number": "", "register_type": "",
            "register_court": "", "register_source": "", "contact_name": "Max Huber", "contact_role": "Geschäftsführer",
            "contact_source": "P1", "email": "office@poolbau-huber.at", "email_source": "P1",
            "phone": "+43 662 123456", "phone_source": "P1", "ecommerce": "YES", "ecommerce_source": "P2",
            "detected_brands": [{"brand": "SPECK", "source_page": "P2"}], "fluidra_brands": [],
            "customer_checks": []}
    base.update(over)
    return base


class FakeProvider:
    name, model = "FAKE", "fake-model"

    def __init__(self, answers: dict | None = None, default=None):
        self.answers, self.default, self.sent, self.batches = answers or {}, default or answer(), [], {}

    def _res(self, r):
        self.sent.append(r)
        bid = int(r.custom_id.split("_")[0][1:])
        return LLMResult(r.custom_id, "SUCCEEDED", text=json.dumps(self.answers.get(bid, self.default)),
                         stop_reason="end_turn", input_tokens=1000, output_tokens=300)

    def send_direct(self, reqs, max_tokens, concurrency):
        return [self._res(r) for r in reqs]

    def submit_batch(self, reqs, max_tokens):
        bid = f"msgbatch_{len(self.batches) + 1}"
        self.batches[bid] = reqs
        return bid

    def batch_ended(self, external_id):
        return True

    def batch_results(self, external_id):
        return [self._res(r) for r in self.batches[external_id]]

    def count_tokens(self, req):
        return 1000
