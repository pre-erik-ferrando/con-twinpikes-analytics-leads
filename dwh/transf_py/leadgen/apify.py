"""Google Maps capture through Apify, actor compass/crawler-google-places (DT.3), behind a replaceable
interface (`Capturer`).

TWP calls the Store actor directly, not a saved Task, so parameters not sent take the actor's
defaults. Hence:
- every parameter is sent explicitly on every call, including those equal to the default;
- the actor build is pinned (apify_actor_build) and only changed on purpose;
- the complete input of every call is stored in ops.search_query_log.

Call contract (DT.3.2): area in structured fields (countryCode + state + county/city/postalCode), never
free text; we send the area's outline instead, and free text only to look up one business in
IMPORT_LIST. Detail page always on; paid add-ons explicitly off; skipClosedPlaces=false (closed listings
are captured and filtered later); a per-search cap and an always-present spending cap per run;
never categoryName nor isAdvertisement.
⚠ Input field names follow the actor's public input schema; check them against the pinned build when
it is fixed (apify_actor_build is [PENDIENTE] in the definition).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Protocol

ACTOR_ID = "compass~crawler-google-places"
API = "https://api.apify.com/v2"


# English country names for IMPORT_LIST's free-text location ("Graz, Austria" is what was tested)
COUNTRY_NAMES = {"AT": "Austria", "DE": "Germany", "CH": "Switzerland", "BE": "Belgium", "NL": "Netherlands"}


def import_location(city: str | None, country: str) -> str:
    """IMPORT_LIST looks up one named business, so its location is free text ("City, Country"): a city
    by name in the structured fields returns nothing (exploration/findings.md, 2026-11-18), and there is
    no outline for a city. Capture areas never use this."""
    return ", ".join(x for x in ((city or "").strip(), COUNTRY_NAMES.get(country.upper(), country)) if x)


def actor_input(term: str, actor_fields: dict, language: str, max_places: int, location_query: str = "") -> dict:
    """The complete, explicit input of one query (one term x one area).

    The area goes as its outline (customGeolocation) when it has one, and then the name fields go empty:
    with names alone the actor (builds 0.14.792-793) marks every place as out of location and returns
    nothing (exploration/findings.md, 2026-11-18). Free text (location_query) only for IMPORT_LIST
    (import_location); it also empties the name fields."""
    outline = None if location_query else actor_fields.get("customGeolocation")
    loc = {k: "" if (outline or location_query) else (actor_fields.get(k) or "")
           for k in ("countryCode", "state", "county", "city", "postalCode")}
    loc["countryCode"] = loc["countryCode"].lower()
    return {
        "searchStringsArray": [term],
        "locationQuery": location_query,
        **loc,
        **({"customGeolocation": outline} if outline else {}),   # left out, not null, when there is none
        "language": language,
        "maxCrawledPlacesPerSearch": int(max_places),
        "searchMatching": "all",
        "placeMinimumStars": "",
        "website": "allPlaces",
        "skipClosedPlaces": False,
        "scrapePlaceDetailPage": True,
        "scrapeTableReservationProvider": False,
        "includeWebResults": False,
        "scrapeDirectories": False,
        "maxQuestions": 0,
        "scrapeContacts": False,
        "maximumLeadsEnrichmentRecords": 0,
        "leadsEnrichmentDepartments": [],
        "maxReviews": 0,
        "reviewsSort": "newest",
        "scrapeReviewsPersonalData": False,
        "maxImages": 0,
        "scrapeImageAuthors": False,
        "scrapeSocialMediaProfiles": {"facebooks": False, "instagrams": False, "youtubes": False,
                                      "tiktoks": False, "twitters": False},
        "allPlacesNoSearchAction": "",
    }


@dataclass
class ActorRun:
    apify_run_id: str
    status: str
    items: list[dict]
    cost_usd: float | None
    started_at: str | None
    finished_at: str | None


class Capturer(Protocol):
    build: str

    def run(self, actor_in: dict, max_charge_usd: float) -> ActorRun: ...


class ApifyCapturer:
    def __init__(self, token: str, build: str, poll_s: float = 10, timeout_s: float = 3600, session=None):
        import requests

        self.token, self.build, self.poll_s, self.timeout_s = token, build, poll_s, timeout_s
        self.http = session or requests.Session()

    def run(self, actor_in: dict, max_charge_usd: float) -> ActorRun:
        params = {"token": self.token, "maxTotalChargeUsd": f"{max_charge_usd:.2f}"}
        if self.build:
            params["build"] = self.build
        r = self.http.post(f"{API}/acts/{ACTOR_ID}/runs", params=params, json=actor_in, timeout=60)
        r.raise_for_status()
        data = r.json()["data"]
        run_id, t0 = data["id"], time.monotonic()
        while data["status"] in ("READY", "RUNNING"):
            if time.monotonic() - t0 > self.timeout_s:
                # stop it on Apify's side too, or it keeps running (and charging) after we give up
                self.http.post(f"{API}/actor-runs/{run_id}/abort", params={"token": self.token}, timeout=60)
                raise TimeoutError(f"Apify run {run_id} still {data['status']} after {self.timeout_s}s: aborted")
            time.sleep(self.poll_s)
            data = self._get(f"{API}/actor-runs/{run_id}", {}, 60)["data"]
        items = self._get(f"{API}/datasets/{data['defaultDatasetId']}/items", {"format": "json", "clean": "true"}, 300)
        return ActorRun(run_id, data["status"], items, data.get("usageTotalUsd"), data.get("startedAt"),
                        data.get("finishedAt"))

    def _get(self, url: str, params: dict, timeout: int):
        """GET that fails on an HTTP error (an error page is not data)."""
        r = self.http.get(url, params={"token": self.token, **params}, timeout=timeout)
        r.raise_for_status()
        return r.json()


# --------------------------------------------------------------------------- reading an item
def item_fields(item: dict) -> dict:
    """The fields of an actor item the engine uses. categoryName is NEVER used: it is contaminated by
    the search term (DT.3.2); the categories come from `categories`."""
    loc = item.get("location") or {}
    return {
        "place_id": item.get("placeId"),
        "name": item.get("title"),
        "address": item.get("address"),
        "street": item.get("street"),
        "city": item.get("city"),
        "postal_code_raw": item.get("postalCode"),
        "phone_raw": item.get("phoneUnformatted") or item.get("phone"),
        "website": item.get("website"),
        "latitude": loc.get("lat"),
        "longitude": loc.get("lng"),
        "categories": list(item.get("categories") or []),
        "main_category": (item.get("categories") or [None])[0],
        "owner_description": item.get("description"),
        "reviews_count": item.get("reviewsCount"),
        "rating": item.get("totalScore"),
        "is_closed_permanently": bool(item.get("permanentlyClosed")),
        "is_closed_temporarily": bool(item.get("temporarilyClosed")),
        "scraped_at": item.get("scrapedAt"),
    }


def is_saturated(n_results: int, cap: int) -> bool:
    """A query saturates when it returns as many results as its cap (DT.3.4)."""
    return cap > 0 and n_results >= cap


def dumps(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)
