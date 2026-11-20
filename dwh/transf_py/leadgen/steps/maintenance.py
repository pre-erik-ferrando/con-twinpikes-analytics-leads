"""REPROCESS_RULES, SCORE_ONLY, CUSTOMER_REMATCH, Salesforce's status effects and the validation helpers.

- REPROCESS_RULES: re-applies the filter and the customer match with the current configuration, with no
  new capture, crawl or model call (free). Mandatory after activating a new profile (review DT.4.4).
  New candidates without a verdict count as UNSURE until the next ENRICH (DT.5.3).
- SCORE_ONLY: rescoring of the already enriched leads (e.g. after a change of points or bands).
- CUSTOMER_REMATCH (review DT.5): after loading a new extract, re-matches the leads in Found, To review
  and Approved. Matching ones become Excluded by engine (customer). Published leads are not touched:
  a match shows in lgn_v_published_customer_conflict.
"""
from __future__ import annotations

import json

from .. import runs
from ..config import precheck
from ..matching import MatchConfig, apply_verdicts, match
from ..state import (RunContext, business_views, current_validations, customer_index, latest, published_ids,
                     sync_human_decisions)
from .capture import RunFailed
from .common import (current_web, deciding_matches, facts_from_google, facts_with_web, is_customer, match_row,
                     run_filter, score_businesses, scoring_context)


def _start(store, run_type, country, profile, by, request) -> tuple[RunContext, object]:
    ctx = RunContext(store, runs.new_id("run"), run_type, country, profile, by)
    ctx.set_state("REQUESTED", request=request)
    sync_human_decisions(ctx)
    ctx.freeze_snapshot()
    ctx.set_state("SNAPSHOT_FROZEN")
    return ctx, ctx.config()


def _rematch(ctx, cfg, ids: set[int], match_pass: str) -> dict[int, object]:
    """Pass 1 with Google data, or pass 2 with the stored web facts when the lead was enriched;
    stored verdicts are reused, new candidates count as UNSURE."""
    store = ctx.store
    index, extracts = customer_index(store, ctx.country)
    mc = MatchConfig.from_config(cfg)
    ext = ",".join(sorted(e["extract_id"] for e in extracts.values())) or None
    views = business_views(store, ctx.country, ids)
    web = current_web(store, ctx.country, ctx.profile, ids)
    prev = deciding_matches(store, ctx.country, ids)
    legal = cfg.legal_forms()
    out, rows = {}, []
    for b, v in views.items():
        facts = facts_with_web(v, web[b], legal) if b in web else facts_from_google(v)
        res = match(facts, index, mc)
        if res.outcome == "CANDIDATES":
            # Verdicts the model already gave: DIFFERENT for every candidate of a NO_MATCH row, or the
            # verdict on the one customer of a CUSTOMER row. Any other candidate is new: UNSURE.
            old = prev.get(b) or {}
            known: dict[str, str] = {}
            if old.get("verdict") == "DIFFERENT":
                known = {c["customer_code"]: "DIFFERENT" for c in json.loads(old.get("candidates_json") or "[]")}
            elif old.get("verdict") in ("SAME", "UNSURE") and old.get("customer_code"):
                known = {old["customer_code"]: old["verdict"]}
            res = apply_verdicts(res, {c.ref: known.get(c.customer.customer_code, "UNSURE") for c in res.candidates})
        out[b] = res
        rows.append(match_row(ctx, res, match_pass, ext))
    store.insert("customer_match_out", rows)
    return out


def run_reprocess(store, country: str, profile: str, by: str, capture_batch_id: str | None = None,
                  score_only: bool = False) -> tuple[RunContext, dict]:
    ctx, cfg = _start(store, "SCORE_ONLY" if score_only else "REPROCESS_RULES", country, profile, by,
                      {"capture_batch_id": capture_batch_id})
    pre = precheck(cfg, needs_scoring=True)
    if pre.blocking:
        ctx.set_state("FAILED", "precheck: " + "; ".join(pre.blocking))
        raise RunFailed(ctx.log[-1])
    ctx.set_state("RUNNING")
    try:
        if capture_batch_id:
            ids = {r["business_id"] for r in store.select("capture_batch_lead", {"capture_batch_id": capture_batch_id})}
        else:
            ids = {r["business_id"] for r in store.select("capture_batch_lead", {"country": country})
                   if r.get("profile") == profile}
        views = business_views(store, country, ids)
        n_incl = None
        if not score_only:
            decisions = run_filter(ctx, cfg, views)
            included = {b for b, d in decisions.items() if d.decision == "INCLUDE"}
            n_incl = len(included)
            _rematch(ctx, cfg, included, "CAPTURE")
            ids = included
        web = current_web(store, country, profile, ids)
        matches = deciding_matches(store, country, ids)
        to_score = {b: v for b, v in views.items() if b in web and not is_customer(matches.get(b))}
        scores = score_businesses(ctx, cfg, to_score, web, scoring_context(ctx, cfg))
        summary = {"businesses": len(views), "included": n_incl, "scored": len(scores)}
    except Exception as e:                    # noqa: BLE001
        ctx.set_state("FAILED", f"{type(e).__name__}: {e}")
        raise
    ctx.set_state("CLOSED", json.dumps(summary))
    return ctx, summary


def run_rematch(store, country: str, by: str, profile: str = "POOL_SPECIALIST") -> tuple[RunContext, dict]:
    ctx, cfg = _start(store, "CUSTOMER_REMATCH", country, profile, by, {"country": country})
    ctx.set_state("RUNNING")
    try:
        # Found, To review and Approved: every lead not excluded, in ANY profile (the customer match is per
        # business, not per profile); published leads are matched too, but only registered (the conflict
        # view shows them), never changed. `profile` only labels the run.
        included = {r["business_id"] for r in latest(
            store.select("rule_evaluation_out", {"country": country}), ("business_id", "profile")).values()
                    if r["decision"] == "INCLUDE"}
        before = deciding_matches(store, country, included)
        res = _rematch(ctx, cfg, included, "REMATCH")
        published = published_ids(store, country)
        newly = [b for b, r in res.items() if r.outcome == "CUSTOMER" and not is_customer(before.get(b))]
        summary = {"rematched": len(res), "new_customers": len([b for b in newly if b not in published]),
                   "published_conflicts": len([b for b in newly if b in published])}
    except Exception as e:                    # noqa: BLE001
        ctx.set_state("FAILED", f"{type(e).__name__}: {e}")
        raise
    ctx.set_state("CLOSED", json.dumps(summary))
    return ctx, summary


# --------------------------------------------------------------------------- validation (CLI while Intelek cannot write)
def to_review(store, capture_batch_id: str) -> list[int]:
    """Scored leads of the batch whose current decision is PENDING or none, not excluded nor customer."""
    batch = store.select("capture_batch", {"capture_batch_id": capture_batch_id})[0]
    country, profile = batch["country"], batch["profile"]
    ids = {r["business_id"] for r in store.select("capture_batch_lead", {"capture_batch_id": capture_batch_id})}
    scored = {r["business_id"] for r in store.select("score_out", {"country": country})
              if r.get("profile") == profile and r["business_id"] in ids}
    vals = current_validations(store, country, profile)
    matches = deciding_matches(store, country, scored)
    return sorted(b for b in scored if vals.get(b, {}).get("decision", "PENDING") == "PENDING"
                  and not is_customer(matches.get(b)))


def approve_rest(store, capture_batch_id: str, by: str, visible: list[int] | None = None,
                 active_filter: str | None = None) -> int:
    """Approves the PENDING leads of the batch: only those shown (`visible`) or, with visible=None,
    "Approve the rest of the batch" (review DT.8: explicit scope, audited)."""
    batch = store.select("capture_batch", {"capture_batch_id": capture_batch_id})[0]
    pending = to_review(store, capture_batch_id)
    ids = [b for b in pending if visible is None or b in set(visible)]
    store.insert("biz_lead_validation", [{
        "business_id": b, "profile": batch["profile"], "country": batch["country"], "run_id": batch["run_id"],
        "capture_batch_id": capture_batch_id, "decision": "ACCEPTED",
        "approval_scope": "REST_OF_BATCH" if visible is None else "LEAD", "active_filter": active_filter,
        "reviewed_by": by} for b in ids])
    return len(ids)


def reject(store, capture_batch_id: str, business_id: int, reason: str, by: str, customer_code: str | None = None,
           subsidiary_code: str | None = None, duplicate_of: int | None = None, comment: str | None = None) -> None:
    batch = store.select("capture_batch", {"capture_batch_id": capture_batch_id})[0]
    cfg = RunContext(store, "", "", batch["country"], batch["profile"], by).config()
    r = cfg.validation_reasons().get(reason)          # only the reasons that apply to the batch's profile
    if r is None:
        known = {x["reason_code"] for x in cfg.rows("biz_cfg_validation_reason")}
        raise ValueError(f"rejection reason {reason} does not apply to profile {batch['profile']}" if reason in known
                         else f"unknown rejection reason {reason}")
    if r.get("requires_customer_code") and not customer_code:
        raise ValueError(f"{reason} needs the customer code")
    if r.get("requires_duplicate_of") and not duplicate_of:
        raise ValueError(f"{reason} needs the business that stays (duplicate_of)")
    store.insert("biz_lead_validation", [{
        "business_id": business_id, "profile": batch["profile"], "country": batch["country"], "run_id": batch["run_id"],
        "capture_batch_id": capture_batch_id, "decision": "EXCLUDED", "exclusion_reason": reason,
        "customer_code": customer_code, "subsidiary_code": subsidiary_code, "duplicate_of": duplicate_of,
        "comment": comment, "approval_scope": "LEAD", "reviewed_by": by}])


def reopen(store, capture_batch_id: str, business_id: int, by: str) -> None:
    """Approved -> To review, only before export (review DT.8). Reopening is inserting a PENDING row."""
    batch = store.select("capture_batch", {"capture_batch_id": capture_batch_id})[0]
    if business_id in published_ids(store, batch["country"]):
        raise ValueError("a Published lead cannot go back to To review")
    # the current status of each export the lead was in (insert-only: the GENERATED row stays after FAILED)
    current = latest(store.select("export_lead_out", {"business_id": business_id}), ("export_id",))
    if any(r["status"] == "GENERATED" for r in current.values()):
        raise ValueError("the lead is in a generated export: confirm it FAILED first")
    store.insert("biz_lead_validation", [{
        "business_id": business_id, "profile": batch["profile"], "country": batch["country"], "run_id": batch["run_id"],
        "capture_batch_id": capture_batch_id, "decision": "PENDING", "approval_scope": "LEAD", "reviewed_by": by}])


# --------------------------------------------------------------------------- Salesforce status (DT.8.4)
def current_sf_status(store, country: str) -> dict[str, dict]:
    """lead_code -> its current state in Salesforce: the row of the latest month (the latest import wins
    within a month)."""
    out: dict[str, dict] = {}
    for r in store.select("sf_status_out", {"country": country}):
        cur = out.get(r["lead_code"])
        if cur is None or (r["status_month"], r["inserted_at"]) >= (cur["status_month"], cur["inserted_at"]):
            out[r["lead_code"]] = r
    return out


def apply_sf_status(store, country: str) -> int:
    """v3.3 DT.8.4: a lead currently DISCARDED in Salesforce is excluded for good, with or without a
    reason; with ALREADY_CUSTOMER it also counts as a failure of the automatic match (a SALESFORCE match
    without customer code: the file does not bring it). CONVERTED, WORKING and NURTURING change nothing:
    the lead stays Published. Idempotent per file and lead."""
    done = {r.get("source_ref") for r in store.select("exclusion_out", {"country": country})
            if r["origin"] == "SALESFORCE"}
    excl, matches = [], []
    for code, r in current_sf_status(store, country).items():
        if r["status"] != "DISCARDED":
            continue
        ref = f"sf:{r['status_file_id']}:{code}"
        if ref in done:
            continue
        reason = r.get("discard_reason") or "DISCARDED"
        excl.append({"business_id": r["business_id"], "country": country, "scope": "ALL", "profile": None,
                     "origin": "SALESFORCE", "reason": reason, "source_ref": ref})
        if reason == "ALREADY_CUSTOMER":
            matches.append({"run_id": "salesforce", "business_id": r["business_id"], "country": country,
                            "profile": None, "match_pass": "MANUAL", "origin": "SALESFORCE", "outcome": "CUSTOMER",
                            "signal": None, "customer_code": None,
                            "reason_text": f"Salesforce status {r['status_month']}: discarded, already a customer"})
    store.insert("exclusion_out", excl)
    store.insert("customer_match_out", matches)
    return len(excl)
