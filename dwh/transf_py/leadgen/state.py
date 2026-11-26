"""Reading the current state of the engine's insert-only tables, and the run context.

Every "current" here is resolved in Python with the same rule as the SQL views: the latest row by
inserted_at for the natural key (argMax).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from . import ddl, runs
from .config import Config
from .identity import pick_master, resolve_alias
from .matching import Customer, CustomerIndex
from .normalize import normalize_text


def latest(rows: list[dict], key: tuple[str, ...]) -> dict[tuple, dict]:
    out: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r.get(c) for c in key)
        if k not in out or r["inserted_at"] >= out[k]["inserted_at"]:
            out[k] = r
    return out


# --------------------------------------------------------------------------- run context
@dataclass
class RunContext:
    store: object
    run_id: str
    run_type: str
    country: str
    profile: str | None
    requested_by: str
    capture_batch_id: str | None = None
    snapshot_id: str | None = None
    snapshot_at: datetime | None = None
    state: str | None = None
    log: list[str] = field(default_factory=list)
    vat_checker: object | None = None        # enrich: vat.VatChecker; None leaves NOT_CHECKED

    def set_state(self, state: str, message: str | None = None, request: dict | None = None) -> None:
        runs.check_transition(self.state, state)
        self.store.insert("pipeline_run", [{
            "run_id": self.run_id, "run_type": self.run_type, "state": state, "country": self.country,
            "profile": self.profile, "capture_batch_id": self.capture_batch_id, "snapshot_id": self.snapshot_id,
            "request_json": json.dumps(request, default=str) if request is not None else "",
            "message": message, "requested_by": self.requested_by}])
        self.state = state
        if message:
            self.log.append(f"{state}: {message}")

    def freeze_snapshot(self) -> None:
        """Per config table, max(inserted_at) now: stg and the steps only see rows up to snapshot_at."""
        self.snapshot_id = runs.new_id("snap")
        self.snapshot_at = self.store.now()
        rows = []
        for name in ddl.CONFIG_TABLES:
            stamps = [r["inserted_at"] for r in self.store.select(name, columns=["inserted_at"])]
            rows.append({"snapshot_id": self.snapshot_id, "run_id": self.run_id, "snapshot_at": self.snapshot_at,
                         "table_name": name, "max_inserted_at": max(stamps) if stamps else None})
        self.store.insert("config_snapshot", rows)

    def adopt_snapshot(self, snapshot_id: str) -> None:
        """A resumed run keeps its snapshot even if the configuration changed meanwhile (DT.2.6)."""
        rows = self.store.select("config_snapshot", {"snapshot_id": snapshot_id})
        if not rows:
            raise ValueError(f"snapshot {snapshot_id} not found")
        self.snapshot_id, self.snapshot_at = snapshot_id, rows[0]["snapshot_at"]

    def config(self) -> Config:
        raw = {name: self.store.select(name) for name in ddl.CONFIG_TABLES}
        return Config.from_rows(raw, self.country, self.profile, self.snapshot_at)


def resume_context(store, run_id: str) -> RunContext:
    rows = store.select("pipeline_run", {"run_id": run_id})
    if not rows:
        raise ValueError(f"run {run_id} not found")
    cur = runs.current_states(rows)[run_id]
    first = min(rows, key=lambda r: r["inserted_at"])
    ctx = RunContext(store, run_id, cur["run_type"], cur["country"], cur["profile"], first["requested_by"],
                     cur.get("capture_batch_id"), state=cur["state"])
    if cur.get("snapshot_id"):
        ctx.adopt_snapshot(cur["snapshot_id"])
    ctx.request = json.loads(first.get("request_json") or "{}")   # type: ignore[attr-defined]
    return ctx


# --------------------------------------------------------------------------- businesses
def aliases(store) -> dict[int, int]:
    """Absorbed business -> the surviving one (one level; resolve_alias follows chains). Per business the
    latest ALIAS or UNALIAS counts: an UNALIAS (a DUPLICATE rejection reopened) undoes the merge."""
    last: dict[int, dict] = {}
    for r in store.select("business_identity_out", {"kind": ["ALIAS", "UNALIAS"]}):
        if r["business_id"] not in last or r["inserted_at"] >= last[r["business_id"]]["inserted_at"]:
            last[r["business_id"]] = r
    return {b: r["other_business_id"] for b, r in last.items() if r["kind"] == "ALIAS"}


def links(store, country: str | None = None) -> dict[str, int]:
    rows = store.select("business_link_out", {"country": country} if country else None)
    first: dict[str, dict] = {}
    for r in rows:                       # a listing is linked once; the earliest link stands
        if r["place_id"] not in first or r["inserted_at"] < first[r["place_id"]]["inserted_at"]:
            first[r["place_id"]] = r
    return {p: r["business_id"] for p, r in first.items()}


def latest_places(store, country: str, place_ids: list[str] | None = None) -> dict[str, dict]:
    where = {"country": country}
    if place_ids is not None:
        where["place_id"] = place_ids
    return {k[0]: v for k, v in latest(store.select("place_norm_out", where), ("place_id",)).items()}


@dataclass
class BusinessView:
    business_id: int
    country: str
    place_ids: list[str]
    master: dict
    categories: list[str]
    reviews_count: int | None
    rating: float | None
    phones: set[str]
    domains: set[str]
    is_closed_permanently: bool
    is_closed_temporarily: bool
    first_seen_at: datetime | None


def business_views(store, country: str, business_ids: set[int] | None = None) -> dict[int, BusinessView]:
    """Businesses with their listings (aliases resolved): master listing, union of categories, max of
    reviews with the rating of that same listing (DT.4.2)."""
    al = aliases(store)
    by_biz: dict[int, list[str]] = {}
    for place_id, bid in links(store, country).items():
        bid = resolve_alias(bid, al)
        if business_ids is None or bid in business_ids:
            by_biz.setdefault(bid, []).append(place_id)
    places = latest_places(store, country, [p for ps in by_biz.values() for p in ps]) if by_biz else {}
    first_seen = {}
    for r in store.select("place_norm_out", {"country": country, "place_id": list(places)}) if places else []:
        if r["place_id"] not in first_seen or r["inserted_at"] < first_seen[r["place_id"]]:
            first_seen[r["place_id"]] = r["inserted_at"]
    out = {}
    for bid, pids in by_biz.items():
        ps = [places[p] for p in pids if p in places]
        if not ps:
            continue
        master = pick_master(ps)
        top = max(ps, key=lambda p: (p.get("reviews_count") or 0, p["place_id"]))
        cats = list(dict.fromkeys(c for p in ps for c in (p.get("categories") or [])))
        out[bid] = BusinessView(
            bid, country, pids, master, cats, top.get("reviews_count"), top.get("rating"),
            {p["phone_e164"] for p in ps if p.get("phone_e164")}, {p["domain"] for p in ps if p.get("domain")},
            # a business is closed only if all its listings are
            all(p.get("is_closed_permanently") for p in ps), any(p.get("is_closed_temporarily") for p in ps),
            min((first_seen[p] for p in pids if p in first_seen), default=None))
    return out


# --------------------------------------------------------------------------- decisions
def current_validations(store, country: str, profile: str | None = None) -> dict[int, dict]:
    """The current decision per business (and profile), following aliases. A DUPLICATE rejection of an
    absorbed business does not follow it: it is the merge itself, not a decision on the surviving one."""
    al = aliases(store)
    where = {"country": country}
    if profile:
        where["profile"] = profile
    out: dict[int, dict] = {}
    for r in store.select("biz_lead_validation", where):
        bid = resolve_alias(r["business_id"], al)
        if bid != r["business_id"] and r["decision"] == "EXCLUDED" and r.get("exclusion_reason") == "DUPLICATE":
            continue
        cur = out.get(bid)
        if cur is None or (r["inserted_at"], r["reviewed_by"]) >= (cur["inserted_at"], cur["reviewed_by"]):
            out[bid] = r
    return out


def permanent_exclusions(store, country: str, profile: str | None) -> dict[int, str]:
    """business_id -> 'ORIGIN:REASON' of its permanent exclusion (DT.8.1): the dbt view lgn_f_exclusion
    (Sales Ops' current EXCLUDED decision, a DISCARDED in Salesforce), Sales Ops first."""
    out: dict[int, str] = {}
    rows = store.read_view("lgn_f_exclusion", {"country": country})
    for r in sorted(rows, key=lambda r: r["origin"] != "HUMAN"):
        if r["scope"] == "PROFILE" and r.get("profile") != profile:
            continue
        out.setdefault(int(r["business_id"]), f"{r['origin']}:{r['reason']}")
    return out


def exported(store, country: str) -> dict[tuple, dict]:
    """(business_id, export_id) -> the latest row of that lead in that export (any status), under the
    surviving business: a lead exported before a merge stays exported (lgn_f_export_lead)."""
    al = aliases(store)
    rows = [{**r, "business_id": resolve_alias(r["business_id"], al)}
            for r in store.select("export_lead_out", {"country": country})]
    return latest(rows, ("business_id", "export_id"))


def published_ids(store, country: str) -> set[int]:
    return {k[0] for k, v in exported(store, country).items() if v["status"] == "UPLOADED"}


def suppressed(store) -> dict[str, set]:
    """The suppression list as Intelek wrote it (biz_suppression_request): business ids, place ids, phones
    and domains."""
    out = {"business_id": set(), "place_id": set(), "phone": set(), "domain": set()}
    for r in store.select("biz_suppression_request"):
        for k in out:
            if r.get(k):
                out[k].add(r[k])
    return out


def is_suppressed(view: BusinessView, sup: dict[str, set]) -> bool:
    return (view.business_id in sup["business_id"] or bool(set(view.place_ids) & sup["place_id"])
            or bool(view.phones & sup["phone"]) or bool(view.domains & sup["domain"]))


def sync_human_decisions(ctx: RunContext) -> int:
    """Turns what Intelek wrote into engine facts, once (idempotent). The exclusion itself is read from
    the validation (exclusions); what is stored:
    - ALREADY_CUSTOMER -> customer_match_out (HUMAN);
      DUPLICATE -> an ALIAS in business_identity_out towards the surviving business (DT.8.1)."""
    store = ctx.store
    human: dict[int, dict] = {}
    for r in store.select("customer_match_out", {"country": ctx.country}):
        if r.get("origin") == "HUMAN" and (r["business_id"] not in human
                                           or r["inserted_at"] >= human[r["business_id"]]["inserted_at"]):
            human[r["business_id"]] = r
    # a correction already stored and still current (a reopened lead RETRACTED it: excluding it again re-adds it)
    have = {(b, r.get("customer_code")) for b, r in human.items() if r["outcome"] == "CUSTOMER"}
    al_done = set(aliases(store))
    matches, alias_rows = [], []
    for bid, v in current_validations(store, ctx.country).items():
        if v["decision"] != "EXCLUDED":
            continue
        reason = v.get("exclusion_reason") or "NOT_VALID"
        if reason == "DUPLICATE" and v.get("duplicate_of") and bid not in al_done:
            al_done.add(bid)
            alias_rows.append({"kind": "ALIAS", "business_id": bid, "other_business_id": v["duplicate_of"],
                               "reason": "HUMAN_DUPLICATE", "run_id": ctx.run_id})
        if reason == "ALREADY_CUSTOMER" and (bid, v.get("customer_code")) not in have:
            have.add((bid, v.get("customer_code")))
            matches.append({"run_id": ctx.run_id, "business_id": bid, "country": ctx.country, "profile": v.get("profile"),
                            "match_pass": "MANUAL", "origin": "HUMAN", "outcome": "CUSTOMER", "signal": "HUMAN",
                            "subsidiary_code": v.get("subsidiary_code"), "customer_code": v.get("customer_code"),
                            "reason_text": f"Sales Ops validation: customer {v.get('customer_code')}"})
    store.insert("customer_match_out", matches)
    store.insert("business_identity_out", alias_rows)
    return len(matches) + len(alias_rows)

# --------------------------------------------------------------------------- customers
def latest_extracts(store, country: str) -> dict[str, dict]:
    """The latest extract of each subsidiary that ships customers with an address in `country`."""
    files = [{**f, "extract_id": f["file_id"]} for f in store.select("upload_file", {"kind": "CUSTOMER_EXTRACT"})]
    out: dict[str, dict] = {}
    for f in files:
        sub = f.get("subsidiary_code") or f.get("country")
        if sub not in out or (f.get("loaded_at") or "") >= (out[sub].get("loaded_at") or ""):
            out[sub] = f
    return out


def customer_index(store, country: str) -> tuple[CustomerIndex | None, dict[str, dict]]:
    """All customers whose address is in the lead's country, from the latest extract of every
    subsidiary (the German extract carries Austrian and Swiss addresses too). None if there is none."""
    extracts = latest_extracts(store, country)
    if not extracts:
        return None, {}
    ids = [e["extract_id"] for e in extracts.values()]
    rows = store.select("customer_norm_out", {"extract_id": ids, "country": country})
    customers = [Customer(r.get("subsidiary_code"), r["customer_code"], r.get("customer_name") or "", r["country"],
                          r.get("name_key_ae") or "", r.get("name_key_a") or "", r.get("postal_code"),
                          normalize_text(r.get("city")) or None, r.get("phone_e164"), r.get("vat_id"),
                          r.get("register_id"), r.get("corporate_email_domain"), r.get("street"), r.get("latitude"),
                          r.get("longitude"), r.get("coordinate_precision"))
                 for r in rows if not r.get("is_test_record")]
    return (CustomerIndex(customers) if extracts else None), extracts


def extract_age_days(extracts: dict[str, dict], country: str, now: datetime) -> float | None:
    """Age of the country's own subsidiary extract (by the declared date, else the load date)."""
    own = [e for e in extracts.values() if e.get("country") == country]
    if not own:
        return None
    e = max(own, key=lambda e: e.get("loaded_at") or "")
    when = e.get("extract_date") or e.get("loaded_at")
    if isinstance(when, str):
        when = datetime.fromisoformat(when)
    if when.tzinfo is None and now.tzinfo is not None:
        when = when.replace(tzinfo=now.tzinfo)
    return (now - when).total_seconds() / 86400
