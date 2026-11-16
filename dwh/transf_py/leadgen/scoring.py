"""Profile score and Rating (DF.8, DT.7). Maximum 37.5; nobody is discarded by score.

Points come from biz_cfg_scoring_rule (per country, profile column for COMPANY_TYPE) and thresholds
from biz_cfg_rating_band; this module only decides the BUCKET of each factor. Order of the breakdown,
the cockpit's: type, brands, zone, digital, contact, reviews -> e.g. [10, 7.5, 7.5, 2.5, 5, 0.5] = 33.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .domain import NO_OWN_WEB_STATUSES, SCORING_FACTORS


@dataclass
class ScoreInput:
    business_id: int
    name: str
    validation: str | None              # VALID | REVIEW (NOT_VALID never reaches scoring)
    company_type: str | None
    competitor_brands: list[str]        # verified on their page
    subregion_code: str | None
    has_ecommerce: bool | None
    own_domain: str | None              # registrable domain, not a shared one
    crawl_status: str | None
    contact_name: str | None
    email: str | None
    phone_web: str | None
    phone_google: str | None
    reviews_count: int | None


@dataclass
class ReviewReference:
    version: str
    p75: float
    median: float
    n: int
    is_provisional: bool


@dataclass
class Component:
    factor: str
    bucket: str
    points: float
    input_value: str | None


@dataclass
class Score:
    business_id: int
    total: float
    rating: str
    components: list[Component] = field(default_factory=list)

    @property
    def breakdown(self) -> list[float]:
        by = {c.factor: c.points for c in self.components}
        return [by[f] for f in SCORING_FACTORS]


def percentile_inc(values: list[float], q: float) -> float:
    """Excel's PERCENTILE.INC (linear interpolation, (n-1)·q), to reproduce TWP's figures."""
    xs = sorted(values)
    if not xs:
        raise ValueError("empty")
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def review_reference(review_counts: list[int | None], version: str, min_n: int, p75_provisional: float,
                     median_provisional: float) -> ReviewReference:
    """Base: VALID leads of the country not matched with a customer, accumulated over all runs.
    Below min_n the provisional cut-offs (Austria's, 34.8 and 10.5) are used."""
    vals = [float(v) for v in review_counts if v is not None]
    if len(vals) < min_n:
        return ReviewReference(version, p75_provisional, median_provisional, len(vals), True)
    return ReviewReference(version, round(percentile_inc(vals, 0.75), 4), round(percentile_inc(vals, 0.5), 4),
                           len(vals), False)


def bucket_company_type(inp: ScoreInput) -> str:
    if inp.validation == "REVIEW" or inp.company_type not in ("BUILDER_INSTALLER_SHOP", "MAINTENANCE_OR_PREFAB"):
        return "WEAK_UNVERIFIED"
    return inp.company_type


def bucket_brands(brands: list[str], brand_groups: dict[str, int]) -> tuple[str, str | None]:
    """The highest group (1 best) among the competitor brands the business works."""
    groups = sorted((brand_groups[b], b) for b in brands if brand_groups.get(b) in (1, 2, 3))
    if not groups:
        return "NONE", None
    g, b = groups[0]
    return f"GROUP_{g}", b


def bucket_zone(subregion_code: str | None, zones: dict[str, str]) -> str:
    return zones.get(subregion_code or "", "NONE")


def bucket_digital(inp: ScoreInput) -> str:
    if inp.has_ecommerce:
        return "ECOMMERCE"
    # A website that does not answer on crawl day still counts as own website (DF.8); social pages do not.
    if inp.own_domain and inp.crawl_status not in NO_OWN_WEB_STATUSES:
        return "OWN_WEBSITE"
    return "NONE"


def bucket_contact(inp: ScoreInput) -> str:
    phone = inp.phone_web or inp.phone_google        # the website's phone, else Google's
    person, email = bool(inp.contact_name), bool(inp.email)
    if person and email and phone:
        return "PERSON_EMAIL_PHONE"
    if email and phone:
        return "EMAIL_PHONE"
    if person or email or phone:
        return "ANY"
    return "NONE"


def bucket_reviews(reviews: int | None, ref: ReviewReference) -> str:
    """Strict: more than P75, more than the median, at least one. 0 or NULL -> NONE."""
    if not reviews:
        return "NONE"
    if reviews > ref.p75:
        return "ABOVE_P75"
    if reviews > ref.median:
        return "ABOVE_MEDIAN"
    return "ANY"


def rate(total: float, bands: list[tuple[str, float]]) -> str:
    """bands sorted from the highest threshold down; comparison is >=."""
    for rating, min_score in bands:
        if total >= min_score - 1e-9:
            return rating
    return bands[-1][0]


def score(inp: ScoreInput, points: dict[tuple[str, str], float], bands: list[tuple[str, float]],
          brand_groups: dict[str, int], zones: dict[str, str], ref: ReviewReference) -> Score:
    brand_bucket, brand = bucket_brands(inp.competitor_brands, brand_groups)
    buckets = [
        ("COMPANY_TYPE", bucket_company_type(inp), inp.company_type),
        ("BRANDS", brand_bucket, brand),
        ("ZONE", bucket_zone(inp.subregion_code, zones), inp.subregion_code),
        ("DIGITAL", bucket_digital(inp), inp.crawl_status),
        ("CONTACT", bucket_contact(inp), None),
        ("REVIEWS", bucket_reviews(inp.reviews_count, ref),
         "no data" if inp.reviews_count is None else f"{inp.reviews_count} (ref {ref.version})"),
    ]
    comps = []
    for factor, bucket, value in buckets:
        if (factor, bucket) not in points:
            raise KeyError(f"no points for {factor}/{bucket}: the precheck should have blocked the run")
        comps.append(Component(factor, bucket, points[(factor, bucket)], value))
    total = round(sum(c.points for c in comps), 2)
    return Score(inp.business_id, total, rate(total, bands), comps)
