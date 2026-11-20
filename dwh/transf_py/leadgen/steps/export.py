"""EXPORT (DT.8.5 + review DT.8): the Salesforce file of the ACCEPTED leads of a capture batch.

Only Approved leads go: current decision ACCEPTED, not exported before (a FAILED export can be
regenerated), still not a customer after the latest match (the monthly rematch disqualifies them), not
excluded. The export is GENERATED; `confirm` marks it UPLOADED (the leads become Published) or FAILED.
While the Market Cockpit is in use, the same file is loaded there (v3.3 DT.8.5).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from .. import export as X
from .. import runs
from ..domain import CURRENCY
from ..state import RunContext, business_views, current_validations, exported, latest, permanent_exclusions
from .common import capture_batch_leads, current_web, deciding_matches, is_customer


def approved_leads(store, country: str, profile: str, business_ids: set[int] | None) -> list[int]:
    vals = current_validations(store, country, profile)
    exp = exported(store, country)
    excl = permanent_exclusions(store, country, profile)
    ids = [b for b, v in vals.items() if v["decision"] == "ACCEPTED" and (business_ids is None or b in business_ids)]
    matches = deciding_matches(store, country, set(ids))
    statuses: dict[int, set[str]] = {}
    for (bid, _export_id), r in exp.items():
        statuses.setdefault(bid, set()).add(r["status"])
    out = []
    for b in ids:
        if statuses.get(b, set()) & {"GENERATED", "UPLOADED"}:      # a lead is exported once
            continue
        if is_customer(matches.get(b)) or b in excl:
            continue
        out.append(b)
    return sorted(out)


def lead_fields(store, cfg, country: str, profile: str, ids: list[int]) -> list[dict]:
    """The fields export columns can point to (same names as lgn_v_salesforce_export)."""
    views = business_views(store, country, set(ids))
    web = current_web(store, country, profile, set(ids))
    scores = {k[0]: v for k, v in latest([r for r in store.select("score_out", {"country": country})
                                          if r.get("profile") == profile and r["business_id"] in set(ids)],
                                         ("business_id",)).items()}
    areas = cfg.search_areas()
    by_code = {a["admin_code"]: a for a in areas.values() if a.get("level") == "SUBREGION"}
    today = date.today()
    out = []
    for b in ids:
        v, w, s = views.get(b), web.get(b, {}), scores.get(b, {})
        if v is None:
            continue
        m = v.master
        sub = by_code.get(m.get("subregion_code") or "")
        state = areas.get(sub.get("parent_area_id")) if sub and sub.get("parent_area_id") else None
        owner = cfg.sales_owner(m.get("subregion_code")) or {}
        phone = w.get("phone_e164") or m.get("phone_e164")
        phone_type = w.get("phone_type") if w.get("phone_e164") else m.get("phone_type")
        out.append({
            "business_id": b, "lead_code": X.lead_code(country, b),
            "contact_name": w.get("contact_name"), "last_name": w.get("contact_name") or m.get("name"),
            "company": w.get("legal_name") or m.get("name"), "company_google": m.get("name"),
            "owner_first_name": owner.get("owner_first_name"), "owner_last_name": owner.get("owner_last_name"),
            "currency": CURRENCY.get(country, "EUR"), "rating": s.get("rating"), "score": s.get("total"),
            "street": m.get("address"), "city": m.get("city"), "zip_code": m.get("postal_code"),
            "province": (state or {}).get("area_name") or (sub or {}).get("region_name"),
            "country_code": country, "subregion_code": m.get("subregion_code"),
            "category_of_interest": cfg.category_of_interest(w.get("offering"), w.get("company_type"),
                                                             w.get("pool_focus")),
            "family_comments": X.family_comments(w.get("validation_reason_text"), list(w.get("fluidra_brands") or [])),
            "phone": phone, "mobile": phone if phone_type == "MOBILE" else None, "email": w.get("email"),
            "website": m.get("website"), "has_ecommerce": w.get("has_ecommerce"),
            "created_date": today.isoformat(), "lead_status": "NEW"})
    return out


def run_export(store, capture_batch_id: str, requested_by: str, out_dir: Path) -> tuple[RunContext, dict]:
    batch = store.select("capture_batch", {"capture_batch_id": capture_batch_id})
    if not batch:
        raise ValueError(f"capture batch {capture_batch_id} not found")
    country, profile = batch[0]["country"], batch[0]["profile"]
    ctx = RunContext(store, runs.new_id("run"), "EXPORT", country, profile, requested_by,
                     capture_batch_id=capture_batch_id)
    ctx.set_state("REQUESTED", request={"capture_batch_id": capture_batch_id})
    ctx.freeze_snapshot()
    ctx.set_state("SNAPSHOT_FROZEN")
    cfg = ctx.config()
    ctx.set_state("RUNNING")
    ids = approved_leads(store, country, profile, capture_batch_leads(store, capture_batch_id))
    if not ids:
        ctx.set_state("CLOSED", "no approved leads to export")
        return ctx, {"exported": 0}
    leads = lead_fields(store, cfg, country, profile, ids)
    no_owner = X.missing_owner(leads)
    if no_owner:
        msg = (f"{len(no_owner)} leads have no Owner (no salesperson for their subregion and no default owner "
               f"in biz_cfg_sales_owner): export blocked")
        ctx.set_state("FAILED", msg)
        return ctx, {"exported": 0, "error": msg, "missing_owner": no_owner}
    maps = {r["map_name"]: cfg.value_map(r["map_name"]) for r in cfg.rows("biz_cfg_value_map")}
    try:
        header, rows = X.build_rows(leads, cfg.export_columns("SALESFORCE"), maps)
    except X.ExportError as e:
        ctx.set_state("FAILED", str(e))
        return ctx, {"exported": 0, "error": str(e)}
    path = X.write_xlsx(header, rows, out_dir, country)
    export_id = runs.new_id("exp")
    store.insert("export_lead_out", [{"export_id": export_id, "business_id": l["business_id"], "country": country,
                                      "profile": profile, "capture_batch_id": capture_batch_id,
                                      "lead_code": l["lead_code"], "status": "GENERATED", "generated_by": requested_by}
                                     for l in leads])
    store.insert("export_log", [{"export_id": export_id, "run_id": ctx.run_id, "country": country,
                                 "target": "SALESFORCE", "file_name": path.name, "n_leads": len(leads),
                                 "status": "GENERATED", "generated_by": requested_by}])
    summary = {"export_id": export_id, "file": str(path), "exported": len(leads)}
    ctx.set_state("CLOSED", json.dumps(summary))
    return ctx, summary


def confirm(store, export_id: str, status: str, by: str) -> int:
    """UPLOADED (the leads become Published) or FAILED (they can be exported again)."""
    if status not in ("UPLOADED", "FAILED"):
        raise ValueError("status must be UPLOADED or FAILED")
    rows = store.select("export_lead_out", {"export_id": export_id})
    if not rows:
        raise ValueError(f"export {export_id} not found")
    current = latest(rows, ("business_id",))
    done = sorted({r["status"] for r in current.values() if r["status"] != "GENERATED"})
    if done:
        # a confirmed export is final: FAILED leads may already be in a new export, UPLOADED ones in Salesforce
        raise ValueError(f"export {export_id} is already {'/'.join(done)}; it can only be confirmed once")
    store.insert("export_lead_out", [{**{k: v for k, v in r.items() if k != "inserted_at"}, "status": status,
                                      "generated_by": by} for r in current.values()])
    log = latest(store.select("export_log", {"export_id": export_id}), ("export_id",))
    store.insert("export_log", [{**{k: v for k, v in r.items() if k != "inserted_at"}, "status": status,
                                 "generated_by": by} for r in log.values()])
    return len(current)
