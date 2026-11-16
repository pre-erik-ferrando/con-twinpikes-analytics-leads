"""Customer matching (DT.5): is this lead already a Fluidra customer?

Hard signals decide on their own: VAT, commercial register, phone, domain (lead website or published
email domain = the customer's corporate email domain; shared domains never count), Fluidra entity.
Soft signals only produce candidates: normalized name (exact on either key), or token_sort_ratio
>= NAME_FUZZY threshold with at least one distinctive word in common.

A candidate is confirmed without the model when the name is exact, or similarity >= NAME_CONFIRM with a
distinctive word, AND the postal code or the city coincide. Coordinates never confirm alone; with
ADDRESS precision and an active COORDINATES rule they count as "same location".
The rest go to the model with the web analysis: at most max_candidates_per_request, C1..Cn.
When in doubt, the business is set aside: UNSURE counts as customer.

Priority when sources disagree: human or cockpit correction > hard signal > model verdict > soft
confirmation (resolve_priority).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from rapidfuzz.fuzz import token_sort_ratio

from .identity import haversine_m
from .normalize import distinctive_tokens, exact_key


@dataclass
class Customer:
    subsidiary_code: str | None
    customer_code: str
    customer_name: str
    country: str                          # country of the address (all subsidiaries are searched)
    name_key_ae: str = ""
    name_key_a: str = ""
    postal_code: str | None = None
    city_norm: str | None = None
    phone_e164: str | None = None
    vat_id: str | None = None
    register_id: str | None = None
    corporate_email_domain: str | None = None
    street: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    coordinate_precision: str | None = None


@dataclass
class LeadFacts:
    business_id: int
    country: str
    name_key_ae: str
    name_key_a: str
    postal_code: str | None = None
    city_norm: str | None = None
    phones: set[str] = field(default_factory=set)
    domains: set[str] = field(default_factory=set)        # website + published email domains
    vat_ids: set[str] = field(default_factory=set)
    register_ids: set[str] = field(default_factory=set)
    latitude: float | None = None
    longitude: float | None = None


@dataclass
class Candidate:
    ref: str                     # C1..C5
    customer: Customer
    similarity: float


@dataclass
class MatchResult:
    business_id: int
    outcome: str                 # CUSTOMER | CANDIDATES | NO_MATCH | NO_EXTRACT
    signal: str | None = None
    customer: Customer | None = None
    similarity: float | None = None
    verdict: str | None = None
    candidates: list[Candidate] = field(default_factory=list)
    n_candidates_dropped: int = 0
    reason_text: str | None = None

    def candidates_json(self) -> str:
        return json.dumps([{"ref": c.ref, "subsidiary_code": c.customer.subsidiary_code,
                            "customer_code": c.customer.customer_code, "customer_name": c.customer.customer_name,
                            "similarity": round(c.similarity, 1)} for c in self.candidates], ensure_ascii=False)


SIGNAL_LABEL = {"VAT": "VAT", "REGISTER": "Commercial register", "PHONE": "Phone", "DOMAIN": "Domain",
                "FLUIDRA_ENTITY": "Fluidra entity", "NAME_CONFIRM": "Name and location",
                "MODEL": "Model verdict", "HUMAN": "Sales Ops validation", "COCKPIT": "Cockpit result"}


def reason_text(signal: str, c: Customer, verdict: str | None = None) -> str:
    """Readable reason with subsidiary, number and name (DT.5.7), e.g.
    'VAT matches customer 12345 (subsidiary AT), Poolbau Beispiel GmbH'."""
    label = SIGNAL_LABEL.get(signal, signal)
    extra = f" [{verdict}]" if verdict else ""
    sub = f" (subsidiary {c.subsidiary_code})" if c.subsidiary_code else ""
    return f"{label} matches customer {c.customer_code}{sub}, {c.customer_name}{extra}"


DEFAULT_SIGNALS = ("VAT", "REGISTER", "PHONE", "DOMAIN", "FLUIDRA_ENTITY", "NAME_EXACT", "NAME_FUZZY",
                   "NAME_CONFIRM")


@dataclass
class MatchConfig:
    shared_domains: set[str]
    stopwords: set[str]
    fuzzy_threshold: float = 85
    confirm_threshold: float = 92
    max_candidates: int = 5
    coordinates_active: bool = False
    coordinates_threshold_m: float = 50
    fluidra_entities: set[str] = field(default_factory=set)   # VAT / register ids of Fluidra companies
    active_signals: set[str] = field(default_factory=lambda: set(DEFAULT_SIGNALS))

    @classmethod
    def from_config(cls, cfg) -> "MatchConfig":
        def thr(sig, default):
            r = cfg.matching_rule(sig)
            return float(r["threshold"]) if r and r.get("threshold") is not None else default
        active = {r["signal"] for r in cfg.rows("biz_cfg_matching_rule")}
        fluidra = set(cfg.param("fluidra_entity_ids", []) or [])
        return cls(shared_domains=cfg.shared_domains(), stopwords=cfg.stopwords(),
                   fuzzy_threshold=thr("NAME_FUZZY", 85), confirm_threshold=thr("NAME_CONFIRM", 92),
                   max_candidates=int(cfg.param("max_candidates_per_request", 5)),
                   coordinates_active="COORDINATES" in active, coordinates_threshold_m=thr("COORDINATES", 50),
                   fluidra_entities=fluidra, active_signals=active or set(DEFAULT_SIGNALS))


class CustomerIndex:
    """Customers of one country, indexed for the hard signals."""

    def __init__(self, customers: list[Customer]):
        self.customers = customers
        self.by: dict[str, dict[str, list[Customer]]] = {"VAT": {}, "REGISTER": {}, "PHONE": {}, "DOMAIN": {}}
        for c in customers:
            for sig, val in (("VAT", c.vat_id), ("REGISTER", c.register_id), ("PHONE", c.phone_e164),
                             ("DOMAIN", c.corporate_email_domain)):
                if val:
                    self.by[sig].setdefault(val, []).append(c)


def _same_location(lead: LeadFacts, c: Customer, mc: MatchConfig) -> bool:
    if lead.postal_code and c.postal_code and lead.postal_code == c.postal_code:
        return True
    if lead.city_norm and c.city_norm and lead.city_norm == c.city_norm:
        return True
    if (mc.coordinates_active and c.coordinate_precision == "ADDRESS" and None not in
            (lead.latitude, lead.longitude, c.latitude, c.longitude)):
        return haversine_m(lead.latitude, lead.longitude, c.latitude, c.longitude) <= mc.coordinates_threshold_m
    return False


def _name_similarity(lead: LeadFacts, c: Customer) -> tuple[float, bool]:
    """(best token_sort_ratio over both keys, exact match on either key)."""
    exact = any(exact_key(a) and exact_key(a) == exact_key(b)
                for a, b in ((lead.name_key_ae, c.name_key_ae), (lead.name_key_a, c.name_key_a)))
    sim = max(token_sort_ratio(lead.name_key_ae, c.name_key_ae), token_sort_ratio(lead.name_key_a, c.name_key_a))
    return (100.0 if exact else sim), exact


def match(lead: LeadFacts, index: CustomerIndex | None, mc: MatchConfig) -> MatchResult:
    if index is None:
        return MatchResult(lead.business_id, "NO_EXTRACT")

    # Fluidra itself (by VAT or register of one of its companies)
    if "FLUIDRA_ENTITY" in mc.active_signals and (lead.vat_ids | lead.register_ids) & mc.fluidra_entities:
        fake = Customer(None, "FLUIDRA", "Fluidra group company", lead.country)
        return MatchResult(lead.business_id, "CUSTOMER", "FLUIDRA_ENTITY", fake,
                           reason_text=reason_text("FLUIDRA_ENTITY", fake))

    # Hard signals
    for sig, values in (("VAT", lead.vat_ids), ("REGISTER", lead.register_ids), ("PHONE", lead.phones),
                        ("DOMAIN", {d for d in lead.domains if d not in mc.shared_domains})):
        if sig not in mc.active_signals:
            continue
        for v in sorted(values):
            hits = [c for c in index.by[sig].get(v, []) if c.country == lead.country]
            if hits:
                c = sorted(hits, key=lambda x: (x.subsidiary_code or "", x.customer_code))[0]
                return MatchResult(lead.business_id, "CUSTOMER", sig, c, reason_text=reason_text(sig, c))

    # Soft signals -> candidates
    lead_tokens = distinctive_tokens(lead.name_key_ae, mc.stopwords) | distinctive_tokens(lead.name_key_a,
                                                                                            mc.stopwords)
    scored: list[tuple[float, bool, Customer]] = []
    for c in index.customers:
        if c.country != lead.country:
            continue
        sim, exact = _name_similarity(lead, c)
        if exact and "NAME_EXACT" in mc.active_signals:
            scored.append((sim, True, c))
            continue
        if "NAME_FUZZY" in mc.active_signals and sim >= mc.fuzzy_threshold:
            c_tokens = distinctive_tokens(c.name_key_ae, mc.stopwords) | distinctive_tokens(c.name_key_a, mc.stopwords)
            if lead_tokens & c_tokens:
                scored.append((sim, False, c))
    if not scored:
        return MatchResult(lead.business_id, "NO_MATCH")

    scored.sort(key=lambda x: (-x[0], x[2].customer_code))
    for sim, exact, c in scored:          # confirmation without the model
        if (exact or sim >= mc.confirm_threshold) and _same_location(lead, c, mc):
            return MatchResult(lead.business_id, "CUSTOMER", "NAME_CONFIRM", c, similarity=sim,
                               reason_text=reason_text("NAME_CONFIRM", c))
    kept = scored[:mc.max_candidates]
    cands = [Candidate(f"C{i + 1}", c, sim) for i, (sim, _e, c) in enumerate(kept)]
    return MatchResult(lead.business_id, "CANDIDATES", candidates=cands,
                       n_candidates_dropped=max(0, len(scored) - len(kept)))


def apply_verdicts(result: MatchResult, verdicts: dict[str, str]) -> MatchResult:
    """Applies the model's verdicts (already checked: one per candidate, missing = UNSURE).
    SAME and UNSURE mark the lead as customer with a readable reason; DIFFERENT lets it continue."""
    if result.outcome != "CANDIDATES":
        return result
    for cand in result.candidates:
        v = verdicts.get(cand.ref, "UNSURE")
        if v in ("SAME", "UNSURE"):
            return MatchResult(result.business_id, "CUSTOMER", "MODEL", cand.customer, similarity=cand.similarity,
                               verdict=v, candidates=result.candidates,
                               n_candidates_dropped=result.n_candidates_dropped,
                               reason_text=reason_text("MODEL", cand.customer, v))
    return MatchResult(result.business_id, "NO_MATCH", verdict="DIFFERENT", candidates=result.candidates,
                       n_candidates_dropped=result.n_candidates_dropped)



PRIORITY = {"HUMAN": 0, "COCKPIT": 0, "VAT": 1, "REGISTER": 1, "PHONE": 1, "DOMAIN": 1, "FLUIDRA_ENTITY": 1,
            "MODEL": 2, "NAME_CONFIRM": 3}


def resolve_priority(results: list[dict]) -> dict | None:
    """Of several match rows of a business (dicts with origin/signal/outcome/inserted_at), the one
    that decides: human/cockpit > hard signal > model > soft confirmation; the latest within a level."""
    def level(r):
        if r.get("origin") in ("HUMAN", "COCKPIT"):
            return 0
        return PRIORITY.get(r.get("signal") or "", 9)
    deciding = [r for r in results if r.get("outcome") == "CUSTOMER" or r.get("origin") in ("HUMAN", "COCKPIT")]
    if not deciding:
        return max(results, key=lambda r: r.get("inserted_at") or "") if results else None
    best = min(level(r) for r in deciding)
    return max((r for r in deciding if level(r) == best), key=lambda r: r.get("inserted_at") or "")
