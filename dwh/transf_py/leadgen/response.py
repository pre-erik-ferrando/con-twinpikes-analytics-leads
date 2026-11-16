"""Checking the model's answer without the model (DT.6.5, DT.10.5).

- Schema: every field present, enums respected, no extra fields; otherwise the request is resent once
  through the normal API, and a second failure makes the lead REVIEW with reason TECHNICAL.
- '' / NOT_APPLICABLE / UNKNOWN become NULL.
- Each claimed value (VAT, register, email, phone, legal name and address, contact, every brand) must
  appear on the page the model cites (normalized comparison) and the page id must exist; otherwise it is
  dropped and counted in n_unsupported_claims.
- VAT: country format (VIES / UID check is a separate, optional step: vat_check_status).
- Email: syntax (MX check optional, injected). Phone: E.164.
- Brands: only values of biz_cfg_brand, each in its own list.
- One verdict per candidate: missing or repeated -> UNSURE; unknown refs dropped and logged.
- ecommerce: YES with a checked source, or a cart/checkout link among the extracted signals.
- Coherence: REVIEW forces company_type WEAK_UNVERIFIED; NOT_VALID forces NULL.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable

from .content import normalized_contains
from .normalize import (normalize_email, normalize_phone, normalize_register, normalize_vat, none_if_sentinel)
from .prompt import OUTPUT_SCHEMA

NULLISH = {"", "NOT_APPLICABLE", "UNKNOWN"}


class SchemaError(ValueError):
    pass


def check_schema(obj, schema=OUTPUT_SCHEMA, path="$"):
    """A minimal strict JSON-schema check for the shapes OUTPUT_SCHEMA uses."""
    t = schema.get("type")
    if t == "object":
        if not isinstance(obj, dict):
            raise SchemaError(f"{path}: expected object")
        props = schema.get("properties", {})
        missing = [k for k in schema.get("required", []) if k not in obj]
        if missing:
            raise SchemaError(f"{path}: missing {', '.join(missing)}")
        if schema.get("additionalProperties") is False:
            extra = [k for k in obj if k not in props]
            if extra:
                raise SchemaError(f"{path}: unexpected {', '.join(extra)}")
        for k, sub in props.items():
            if k in obj:
                check_schema(obj[k], sub, f"{path}.{k}")
    elif t == "array":
        if not isinstance(obj, list):
            raise SchemaError(f"{path}: expected array")
        for i, item in enumerate(obj):
            check_schema(item, schema["items"], f"{path}[{i}]")
    elif t == "string":
        if not isinstance(obj, str):
            raise SchemaError(f"{path}: expected string")
        if "enum" in schema and obj not in schema["enum"]:
            raise SchemaError(f"{path}: {obj!r} not in enum")


@dataclass
class CheckedAnswer:
    validation: str
    review_reason: str | None
    validation_reason_text: str | None
    offering: str | None
    pool_focus: str | None
    company_type: str | None
    legal_name: str | None = None
    legal_address: str | None = None
    vat_id: str | None = None
    register_id: str | None = None
    register_type: str | None = None
    register_court: str | None = None
    contact_name: str | None = None
    contact_role: str | None = None
    email: str | None = None
    phone_e164: str | None = None
    phone_type: str | None = None
    has_ecommerce: bool | None = None
    detected_brands: list[str] = field(default_factory=list)
    fluidra_brands: list[str] = field(default_factory=list)
    verdicts: dict[str, str] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)          # field -> page URL
    n_unsupported_claims: int = 0
    dropped_refs: list[str] = field(default_factory=list)


def _nz(v):
    v = none_if_sentinel(v)
    return None if v is None or v.upper() in NULLISH else v


def _digits(s: str) -> str:
    return re.sub(r"\D", "", s or "")


def check_answer(raw: dict, pages: dict[str, tuple[str, str]], country: str, competitor_brands: list[dict],
                 fluidra_brands: list[dict], candidate_refs: list[str], cart_signal: bool,
                 mx_check: Callable[[str], bool] | None = None) -> CheckedAnswer:
    """raw: the parsed JSON (already schema-checked). pages: page_id -> (url, text) of what was sent."""
    unsupported = 0
    sources: dict[str, str] = {}

    def page_text(pid):
        p = pages.get((pid or "").strip())
        return p[1] if p else None

    def supported(fieldname, value, source_key, compare=None) -> bool:
        nonlocal unsupported
        if value is None:
            return False
        text = page_text(raw.get(source_key))
        ok = text is not None and (compare(text, value) if compare else normalized_contains(text, value))
        if ok:
            sources[fieldname] = pages[raw[source_key].strip()][0]
        else:
            unsupported += 1
        return ok

    validation = raw["validation"]
    ans = CheckedAnswer(validation=validation, review_reason="INSUFFICIENT_EVIDENCE" if validation == "REVIEW" else None,
                        validation_reason_text=_nz(raw.get("validation_reason")), offering=_nz(raw.get("offering")),
                        pool_focus=_nz(raw.get("pool_focus")), company_type=_nz(raw.get("company_type")))
    if validation == "REVIEW":
        ans.company_type, ans.offering, ans.pool_focus = "WEAK_UNVERIFIED", None, None
    elif validation == "NOT_VALID":
        ans.company_type, ans.offering, ans.pool_focus = None, None, None

    for f in ("legal_name", "legal_address"):
        v = _nz(raw.get(f))
        if supported(f, v, f"{f}_source"):
            setattr(ans, f, v)

    vat_raw = _nz(raw.get("vat_id"))
    if vat_raw and supported("vat_id", vat_raw, "vat_id_source",
                             lambda t, v: _digits(v) and _digits(v) in _digits(t)):
        ans.vat_id = normalize_vat(vat_raw, country)

    reg_raw = _nz(raw.get("register_number"))
    if reg_raw and supported("register", reg_raw, "register_source",
                             lambda t, v: _digits(v) and _digits(v) in _digits(t)):
        ans.register_type, ans.register_court = _nz(raw.get("register_type")), _nz(raw.get("register_court"))
        ans.register_id = normalize_register(reg_raw, ans.register_type, country, ans.register_court)

    name = _nz(raw.get("contact_name"))
    if supported("contact", name, "contact_source"):
        ans.contact_name, ans.contact_role = name, _nz(raw.get("contact_role"))

    email = normalize_email(_nz(raw.get("email")))
    if email and supported("email", email, "email_source", lambda t, v: v in t.lower()):
        if mx_check is None or mx_check(email.split("@", 1)[1]):
            ans.email = email

    phone_raw = _nz(raw.get("phone"))
    if phone_raw:
        e164, kind = normalize_phone(phone_raw, country)
        # the phone may be written with spaces or a national prefix on the page: compare digits tails
        if e164 and supported("phone", phone_raw, "phone_source",
                              lambda t, v: _digits(v)[-7:] and _digits(v)[-7:] in _digits(t)):
            ans.phone_e164, ans.phone_type = e164, kind

    eco = raw.get("ecommerce")
    if eco == "YES" and page_text(raw.get("ecommerce_source")) is not None:
        ans.has_ecommerce = True
        sources["ecommerce"] = pages[raw["ecommerce_source"].strip()][0]
    elif cart_signal:
        ans.has_ecommerce = True
    elif eco == "NO":
        ans.has_ecommerce = False

    def brands(items, allowed: list[dict]):
        nonlocal unsupported
        by_name = {b["brand"].upper(): b for b in allowed}
        kept = []
        for it in items:
            b = by_name.get(re.sub(r"\s*\(.*\)$", "", it["brand"]).strip().upper())
            text = page_text(it.get("source_page"))
            names = [b["brand"], *(b.get("aliases") or [])] if b else []
            if b and text is not None and any(normalized_contains(text, n) for n in names):
                if b["brand"] not in kept:
                    kept.append(b["brand"])
            else:
                unsupported += 1
        return kept

    ans.detected_brands = brands(raw.get("detected_brands", []), competitor_brands)
    ans.fluidra_brands = brands(raw.get("fluidra_brands", []), fluidra_brands)

    seen: dict[str, list[str]] = {}
    for chk in raw.get("customer_checks", []):
        ref = chk["candidate_ref"].strip().upper()
        if ref not in candidate_refs:
            ans.dropped_refs.append(ref)
            continue
        seen.setdefault(ref, []).append(chk["verdict"])
    ans.verdicts = {ref: (seen[ref][0] if len(seen.get(ref, [])) == 1 else "UNSURE") for ref in candidate_refs}

    ans.sources, ans.n_unsupported_claims = sources, unsupported
    return ans


def technical_review(reason: str = "TECHNICAL") -> CheckedAnswer:
    """The answer recorded when the model failed twice, or for a lead with no usable website."""
    return CheckedAnswer(validation="REVIEW", review_reason=reason, validation_reason_text=None, offering=None,
                         pool_focus=None, company_type="WEAK_UNVERIFIED")
