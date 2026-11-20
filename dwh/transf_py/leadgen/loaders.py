"""File loaders: customer extracts (DT.5.1), Salesforce's monthly status file (DT.8.4), official
boundaries (DT.4.1) and own lists (review DT.3.5).

Every file lands as received (landing.*_file + *_row, columns as a JSON map of text); its mapping and
normalization are applied with biz_cfg_source_column, so a new format is a configuration row, not code.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import runs
from .identity import resolve_alias
from .normalize import (email_domain, name_keys, none_if_sentinel, normalize_email, normalize_phone,
                        normalize_postal_code, normalize_register, normalize_vat, strip_diacritics)
from .state import aliases

COUNTRY_NAMES = {
    "deutschland": "DE", "germany": "DE", "allemagne": "DE", "duitsland": "DE", "de": "DE",
    "osterreich": "AT", "oesterreich": "AT", "austria": "AT", "autriche": "AT", "oostenrijk": "AT", "at": "AT",
    "schweiz": "CH", "switzerland": "CH", "suisse": "CH", "svizzera": "CH", "zwitserland": "CH", "ch": "CH",
    "belgien": "BE", "belgium": "BE", "belgique": "BE", "belgie": "BE", "be": "BE",
    "niederlande": "NL", "netherlands": "NL", "nederland": "NL", "pays-bas": "NL", "nl": "NL",
    # car-plate codes, as in the ERP extracts of Fluidra Austria and Fluidra Deutschland (A, D, B)
    "a": "AT", "d": "DE", "b": "BE",
}


def country_to_iso(value) -> str | None:
    v = none_if_sentinel(value)
    return COUNTRY_NAMES.get(strip_diacritics(v).lower().strip()) if v else None


def read_table(path: Path, sheet: str | int | None = 0) -> list[dict]:
    """A CSV or Excel file as a list of {header: text}. Every cell is read as text."""
    import pandas as pd

    if path.suffix.lower() in (".csv", ".txt"):
        raw = path.read_bytes()
        enc = "utf-8-sig"
        try:
            raw.decode(enc)
        except UnicodeDecodeError:
            enc = "cp1252"
        df = pd.read_csv(path, dtype=str, sep=None, engine="python", encoding=enc, keep_default_na=False)
    else:
        df = pd.read_excel(path, dtype=str, sheet_name=sheet, keep_default_na=False)
    df.columns = [str(c).strip() for c in df.columns]
    return [{k: (None if v == "" else v) for k, v in row.items()} for row in df.to_dict(orient="records")]


def apply_mapping(row: dict, mapping: list[dict], country: str, value_maps: dict[str, dict[str, str]]) -> dict:
    """source column -> target column with its transform (biz_cfg_source_column)."""
    out = {}
    for m in mapping:
        raw = row.get(m["source_column"])
        t = m.get("transform") or "NONE"
        if t == "TRIM":
            v = none_if_sentinel(raw)
        elif t == "UPPER":
            v = none_if_sentinel(raw)
            v = v.upper() if v else None
        elif t == "POSTAL_CODE":
            v = normalize_postal_code(raw, country)
        elif t == "PHONE_E164":
            v = normalize_phone(raw, country)[0]
        elif t == "VAT":
            v = normalize_vat(raw, country)
        elif t == "COUNTRY_NAME_TO_ISO":
            v = country_to_iso(raw)
        elif t == "DECIMAL_COMMA":
            v = none_if_sentinel(raw)
            v = float(v.replace(".", "").replace(",", ".")) if v and "," in v else (float(v) if v else None)
        elif t == "VALUE_MAP":
            v = value_maps.get(m.get("value_map") or "", {}).get(none_if_sentinel(raw) or "")
        else:
            v = raw
        out[m["target_column"]] = v
    return out


_REPEATED = re.compile(r"^(\d)\1+$")


def is_test_record(c: dict, raw_postal, patterns: list[str]) -> bool:
    """[PROPUESTA] code or phone of one repeated digit, a test-like name, or a non-numeric postal code."""
    code = re.sub(r"\D", "", c.get("customer_code") or "")
    phone = re.sub(r"\D", "", c.get("phone_raw") or c.get("phone") or "")
    if (code and _REPEATED.match(code)) or (phone and _REPEATED.match(phone)):
        return True
    name = (c.get("customer_name") or "").lower()
    if any(re.search(rf"\b{re.escape(p)}\b", name) for p in patterns):
        return True
    # 'XXX' and the like: a postal code with no digit at all. none_if_sentinel is not used here because
    # it would turn exactly these values into "no postal code".
    raw_postal = str(raw_postal).strip() if raw_postal is not None else ""
    return bool(raw_postal) and not re.search(r"\d", raw_postal)


def load_customers(store, cfg, path: Path, country: str, source_system: str, subsidiary_code: str | None,
                   extract_date: str | None, loaded_by: str, sheet: str | int | None = 0) -> dict:
    mapping = cfg.source_columns(source_system)
    if not mapping:
        raise ValueError(f"no biz_cfg_source_column rows for source system {source_system}")
    rows = read_table(path, sheet)
    missing = sorted({m["source_column"] for m in mapping} - set(rows[0] if rows else {}))
    if missing:
        raise ValueError(f"{path.name}: columns missing for {source_system}: {', '.join(missing)}")
    extract_id = runs.new_id("ext")
    store.insert("customer_extract_file", [{"extract_id": extract_id, "country": country,
                                            "subsidiary_code": subsidiary_code or country,
                                            "source_system": source_system, "extract_date": extract_date,
                                            "file_name": path.name, "loaded_by": loaded_by}])
    store.insert("customer_extract_row", [{"extract_id": extract_id, "source_row_id": str(i),
                                           "row_json": json.dumps(r, ensure_ascii=False)} for i, r in enumerate(rows)])
    maps = {r["map_name"]: cfg.value_map(r["map_name"]) for r in cfg.rows("biz_cfg_value_map")}
    legal = cfg.legal_forms()
    patterns = cfg.param("customer_test_name_patterns", ["test", "prueba", "dummy", "xxx"])
    precision = next((m.get("coordinate_precision") for m in mapping if m["target_column"] in ("latitude", "longitude")),
                     None)
    pc_col = next((m["source_column"] for m in mapping if m["target_column"] == "postal_code"), None)
    phone_col = next((m["source_column"] for m in mapping if m["target_column"] == "phone"), None)
    out = []
    for i, r in enumerate(rows):
        c = apply_mapping(r, mapping, country, maps)
        c["phone_raw"] = r.get(phone_col) if phone_col else None
        addr_country = c.get("address_country") or country
        if not c.get("customer_code"):
            continue
        ae, a = name_keys(c.get("customer_name"), legal)
        lat, lon = c.get("latitude"), c.get("longitude")
        out.append({
            "extract_id": extract_id, "source_row_id": str(i), "country": addr_country,
            "subsidiary_code": subsidiary_code or country, "customer_code": str(c["customer_code"]),
            "customer_name": c.get("customer_name"), "name_key_ae": ae, "name_key_a": a, "street": c.get("street"),
            # postal codes and phones are re-normalized with the ADDRESS country (DT.5.1.1)
            "postal_code": normalize_postal_code(r.get(pc_col) if pc_col else c.get("postal_code"), addr_country),
            "city": c.get("city"), "phone_e164": normalize_phone(c.get("phone"), addr_country)[0] or c.get("phone"),
            "vat_id": normalize_vat(c.get("vat_id"), addr_country),
            "register_id": normalize_register(c.get("register_number"), c.get("register_type"), addr_country,
                                              c.get("register_court")),
            "email": normalize_email(c.get("email")),
            "corporate_email_domain": (c.get("corporate_email_domain") or email_domain(c.get("email")) or None),
            "customer_class": c.get("customer_class"), "sales_rep_name": c.get("sales_rep_name"),
            "latitude": float(lat) if lat not in (None, "") else None,
            "longitude": float(lon) if lon not in (None, "") else None,
            "coordinate_precision": precision if lat not in (None, "") else None,
            "is_test_record": int(is_test_record(c, r.get(pc_col) if pc_col else None, patterns))})
    shared = cfg.shared_domains("EMAIL_PROVIDER")
    for c in out:          # a generic mailbox is not a corporate domain
        if c["corporate_email_domain"] in shared:
            c["corporate_email_domain"] = None
    store.insert("customer_norm_out", out)
    by_country = {}
    for c in out:
        by_country[c["country"]] = by_country.get(c["country"], 0) + 1
    return {"extract_id": extract_id, "rows": len(rows), "customers": len(out),
            "test_records": sum(c["is_test_record"] for c in out), "by_address_country": by_country}


def parse_lead_code(code: str | None) -> tuple[str | None, int | None]:
    """'AT-1234567' -> ('AT', 1234567). Anything else (e.g. the cockpit's old AT-NL-002) does not resolve."""
    m = re.fullmatch(r"([A-Z]{2})-(\d+)", (code or "").strip())
    return (m.group(1), int(m.group(2))) if m else (None, None)


SF_STATUS_REQUIRED = ("lead_code", "company_name", "status", "status_month", "subregion_code")


def load_sf_status(store, cfg, path: Path, loaded_by: str) -> dict:
    """Salesforce's monthly status file (v3.3 DT.8.4): lands as received, then each row is mapped with
    biz_cfg_source_column (SF_STATUS) into sf_status_out. A lead code the tool never exported is a
    warning and is not loaded; a subregion that differs from the lead's is a warning (the lead keeps its
    own). The effects (exclusions) are applied by maintenance.apply_sf_status."""
    from .state import latest

    mapping = cfg.source_columns("SF_STATUS")
    if not mapping:
        raise ValueError("no biz_cfg_source_column rows for SF_STATUS")
    rows = read_table(path)
    have = set(rows[0]) if rows else set()
    missing = sorted(m["source_column"] for m in mapping
                     if m["target_column"] in SF_STATUS_REQUIRED and m["source_column"] not in have)
    if missing:
        raise ValueError(f"{path.name}: required columns missing: {', '.join(missing)}")
    maps = {r["map_name"]: cfg.value_map(r["map_name"]) for r in cfg.rows("biz_cfg_value_map")}
    al = aliases(store)
    exported = {r["lead_code"]: r for r in store.select("export_lead_out")}
    link = latest(store.select("business_link_out"), ("place_id",))
    places_of: dict[int, list[str]] = {}
    for (pid,), r in link.items():
        places_of.setdefault(r["business_id"], []).append(pid)
    sub_of = {r["place_id"]: r.get("subregion_code") for r in store.select("place_norm_out")}

    file_id = runs.new_id("sfs")
    mapped = [apply_mapping(r, mapping, "", maps) for r in rows]
    months = sorted({c.get("status_month") for c in mapped if c.get("status_month")})
    store.insert("sf_status_file", [{"status_file_id": file_id, "status_month": months[-1] if months else None,
                                     "file_name": path.name, "loaded_by": loaded_by}])
    store.insert("sf_status_row", [{"status_file_id": file_id, "source_row_id": str(i),
                                    "lead_code": c.get("lead_code"), "row_json": json.dumps(r, ensure_ascii=False)}
                                   for i, (r, c) in enumerate(zip(rows, mapped))])
    out, unknown, bad_status, zone_mismatch = [], [], [], []
    for c in mapped:
        code = c.get("lead_code")
        country, bid = parse_lead_code(code)
        if code not in exported or bid is None:
            unknown.append(code)
            continue
        if c.get("status") is None:
            bad_status.append(code)
            continue
        bid = resolve_alias(bid, al)       # an absorbed business's code becomes the survivor's
        own = {sub_of.get(p) for p in places_of.get(bid, [])} - {None}
        if c.get("subregion_code") and own and c["subregion_code"] not in own:
            zone_mismatch.append(code)
        out.append({"status_file_id": file_id, "lead_code": code, "business_id": bid, "country": country,
                    "sf_lead_id": c.get("sf_lead_id"), "company_name": c.get("company_name"),
                    "status": c["status"], "status_month": c.get("status_month") or "",
                    "subregion_code": c.get("subregion_code"), "discard_reason": c.get("discard_reason")})
    store.insert("sf_status_out", out)
    return {"status_file_id": file_id, "rows": len(rows), "loaded": len(out),
            "unknown_lead_codes": unknown, "unmapped_status": bad_status, "zone_mismatch": zone_mismatch}


def load_geo(store, path: Path, country: str, level: str, source: str, version: str, code_prop: str,
             name_prop: str, parent_prop: str | None = None, keep: set[str] | None = None) -> dict:
    """A GeoJSON FeatureCollection of official boundaries -> landing.reference_geo.

    keep: TWP's district codes (DT.4.1: they must be the ones of TWP's market model). Features with
    another code are skipped, so overlapping areas do not compete (Statistik Austria has Vienna as 900
    and as its city districts 901-923; TWP uses 900). None loads every feature."""
    data = json.loads(path.read_text(encoding="utf-8"))
    rows, skipped = [], []
    for f in data.get("features", []):
        props = f.get("properties") or {}
        code = str(props.get(code_prop))
        if keep is not None and code not in keep:
            skipped.append(code)
            continue
        rows.append({"source": source, "country": country, "version": version, "admin_level": level,
                     "admin_code": code, "admin_name": props.get(name_prop),
                     "parent_code": str(props.get(parent_prop)) if parent_prop and props.get(parent_prop) else None,
                     "geometry_json": json.dumps(f.get("geometry"))})
    store.insert("reference_geo", rows)
    loaded = {r["admin_code"] for r in rows}
    return {"loaded": len(rows), "skipped": sorted(skipped),
            "missing": sorted(keep - loaded) if keep is not None else []}


def load_import_list(store, path: Path, country: str, profile: str, loaded_by: str) -> tuple[str, list[dict]]:
    """[PROPUESTA] An own list: a name column and, if present, city, country, phone and website. Column
    names as in the inputs Excel's Import list tab (company_name…) or loose ones; the whole row, list_source
    included, is kept as received in import_list_row."""
    rows = read_table(path)
    cols = {k.lower(): k for k in (rows[0] if rows else {})}
    name_col = next((cols[c] for c in ("company_name", "name", "company", "firma", "empresa", "nombre") if c in cols), None)
    if name_col is None:
        raise ValueError(f"{path.name}: no name column (name, company, firma…)")
    pick = lambda r, *keys: next((r.get(cols[k]) for k in keys if k in cols), None)   # noqa: E731
    file_id = runs.new_id("imp")
    store.insert("import_list_file", [{"import_file_id": file_id, "country": country, "profile": profile,
                                       "file_name": path.name, "loaded_by": loaded_by}])
    store.insert("import_list_row", [{"import_file_id": file_id, "source_row_id": str(i),
                                      "row_json": json.dumps(r, ensure_ascii=False)} for i, r in enumerate(rows)])
    out = [{"name": r[name_col], "city": pick(r, "city", "ort", "stadt", "ciudad"),
            "phone": pick(r, "phone", "telefon", "tel", "telefono"),
            "website": pick(r, "website", "web", "url")} for r in rows if r.get(name_col)]
    return file_id, out
