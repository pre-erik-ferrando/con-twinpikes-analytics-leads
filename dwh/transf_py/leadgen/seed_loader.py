"""Loading the configuration seeds (DT.2.5 as revised).

Two inputs, the same rules:
- `seeds/*.csv` (this package): the values the definition states and our [PROPUESTA] defaults, one CSV
  per biz_cfg_* table with LOGICAL column names. Array cells use '|' as separator.
- TWP's inputs Excel (DF.14), converted by `excel_to_rows` tab by tab.

A load is a seed_version: rows go in with updated_by = 'seed:<version>' and ONLY keys that do not exist
yet are inserted, so a seed never overwrites what was edited in Intelek; overwriting needs
`overwrite=True` and is recorded in ops.seed_load. Every closed domain is validated before anything is
written; a tab or column the active scope needs and the file lacks fails with a clear message instead
of loading empty (review, Annex B).
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from . import ddl
from .normalize import normalize_category, normalize_text, strip_diacritics

SEEDS = Path(__file__).resolve().parent / "seeds"


def _cast(col: ddl.Col, value):
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return [] if col.type.startswith("Array") else None
    t = col.type
    if t.startswith("Array"):
        return [v.strip() for v in str(value).split("|") if v.strip()] if not isinstance(value, list) else value
    if "UInt" in t or t.startswith("Int") or "Int" in t.replace("Nullable(", ""):
        return int(float(str(value).replace(",", ".")))
    if "Float" in t or "Decimal" in t:
        return float(str(value).replace(",", "."))
    return str(value)


def derive(table: str, row: dict) -> dict:
    """Columns computed from others with the engine's own functions."""
    if table == "biz_cfg_category_rule" and not row.get("category_normalized"):
        row["category_normalized"] = normalize_category(row.get("category"))
    if table == "biz_cfg_title_rule" and not row.get("pattern_normalized"):
        row["pattern_normalized"] = normalize_text(row.get("pattern"))
    if table == "biz_cfg_pool_signal":
        row["stem"] = normalize_text(row.get("stem"))
    if table == "biz_cfg_legal_form":
        row["form_normalized"] = normalize_text(row.get("form_normalized"))
    if table == "biz_cfg_brand" and row.get("brand"):
        row["brand"] = row["brand"].strip().upper()
    return row


def typed_rows(table: str, rows: list[dict]) -> list[dict]:
    t = ddl.CONFIG_TABLES[table]
    out = []
    for r in rows:
        unknown = set(r) - set(t.names)
        if unknown:
            raise ValueError(f"{table}: unknown columns {sorted(unknown)}")
        typed = {c.name: _cast(c, r.get(c.name)) for c in t.columns if c.name in r}
        typed.setdefault("active", 1)
        if typed.get("active") is None:
            typed["active"] = 1
        out.append(derive(table, typed))
    ddl.validate_domains({table: out})
    return out


def read_csv_seeds(folder: Path = SEEDS) -> dict[str, list[dict]]:
    out = {}
    for path in sorted(folder.glob("biz_cfg_*.csv")):
        table = path.stem
        if table not in ddl.CONFIG_TABLES:
            raise ValueError(f"{path.name}: no such configuration table")
        with path.open(encoding="utf-8", newline="") as f:
            out[table] = typed_rows(table, list(csv.DictReader(f)))
    return out


def load(store, tables: dict[str, list[dict]], seed_version: str, loaded_by: str, file_name: str,
         overwrite: bool = False) -> dict[str, int]:
    """Inserts the keys that do not exist yet (all of them with overwrite=True)."""
    done = {}
    for table, rows in tables.items():
        t = ddl.CONFIG_TABLES[table]
        existing = {tuple(r.get(k) for k in t.key) for r in store.select(table, columns=list(t.key))}
        new = [r for r in rows if overwrite or tuple(r.get(k) for k in t.key) not in existing]
        seen, unique = set(), []
        for r in new:                         # the same key twice in one input: the last one wins
            k = tuple(r.get(c) for c in t.key)
            if k in seen:
                unique = [u for u in unique if tuple(u.get(c) for c in t.key) != k]
            seen.add(k)
            unique.append(r)
        store.insert(table, [{**r, "updated_by": f"seed:{seed_version}"} for r in unique])
        store.insert("seed_load", [{"seed_version": seed_version, "table_name": table, "n_rows": len(unique),
                                    "file_name": file_name, "overwrite": int(overwrite), "loaded_by": loaded_by}])
        done[table] = len(unique)
    return done


def load_csv_seeds(store, loaded_by: str, overwrite: bool = False) -> dict[str, int]:
    version = (SEEDS / "VERSION").read_text().strip()
    return load(store, read_csv_seeds(), version, loaded_by, "seeds/*.csv", overwrite)


# --------------------------------------------------------------------------- TWP's inputs Excel (DF.14)
# Header aliases per logical column (case and accents ignored). ⚠ Written from the definition's
# description of the tabs: check them against the frozen snapshot of the Excel (v4) before the first
# load; a missing header fails with the list of headers found.
def _key(s) -> str:
    return re.sub(r"[^a-z0-9]+", " ", strip_diacritics(str(s or "")).lower()).strip()


H = {
    "country": ["country", "pais", "land"],
    "region": ["region", "state", "bundesland", "kanton", "province", "provincie"],
    "region_code": ["region code", "state code"],
    "subregion": ["subregion", "district", "bezirk", "kreis", "arrondissement", "corop", "subregion name"],
    "subregion_code": ["subregion code", "district code", "code", "admin code", "nuts", "refnis"],
    "subregion_type": ["subregion type", "type"],
    "language": ["language", "default language", "idioma"],
    "zone": ["zone", "quadrant", "cuadrante"],
    "market_eur": ["market", "market eur", "market size", "market size eur"],
    "share": ["share", "fluidra share", "cuota"],
    "profile": ["profile", "perfil"],
    "keyword": ["keyword", "term", "search term", "keywords"],
    "preselected": ["preselected", "default", "preseleccionado"],
    "category": ["category", "google category", "categoria"],
    "classification": ["classification", "rule", "action", "clasificacion"],
    "pattern": ["pattern", "name", "exclusion", "term"],
    "match_type": ["match type", "match"],
    "class": ["class", "rule class", "hard soft"],
    "reason": ["reason", "reason code", "motivo"],
    "stem": ["stem", "word", "pool word", "root"],
    "brand": ["brand", "marca"],
    "owner": ["owner", "type", "competitor fluidra"],
    "group": ["group", "brand group", "grupo"],
    "aliases": ["aliases", "alias"],
    "note": ["note", "disambiguation", "disambiguation note"],
    "code": ["code", "reason code", "codigo"],
    "label": ["label", "reason", "motivo", "description"],
    "first_name": ["owner first name", "first name", "sales rep first name"],
    "last_name": ["owner last name", "last name", "sales rep last name", "sales rep"],
}


def _cols(header: list[str], wanted: list[str], required: list[str], tab: str) -> dict[str, str]:
    norm = {_key(h): h for h in header if h is not None}
    out = {}
    for w in wanted:
        hit = next((norm[a] for a in H[w] if a in norm), None)
        if hit:
            out[w] = hit
    missing = [w for w in required if w not in out]
    if missing:
        raise ValueError(f"tab {tab!r}: no column for {', '.join(missing)}; headers found: {', '.join(map(str, header))}")
    return out


def _iso(country: str | None) -> str | None:
    from .loaders import country_to_iso
    return country_to_iso(country) or (country.strip().upper() if country and len(country.strip()) == 2 else None)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", strip_diacritics(s).upper()).strip("_")


def excel_to_rows(path: Path) -> dict[str, list[dict]]:
    """TWP's inputs Excel -> rows per biz_cfg_* table (logical names, untyped). Reference-only tabs
    (Leads n8n table) and the column contracts of the files (Customer file, Load call results,
    Salesforce and MC export, Import list), which are maintained as seeds, are not read."""
    from openpyxl import load_workbook

    wb = load_workbook(path, read_only=True, data_only=True)
    tabs = {_key(n): n for n in wb.sheetnames}

    def sheet(*names, required=True):
        for n in names:
            if _key(n) in tabs:
                rows = list(wb[tabs[_key(n)]].iter_rows(values_only=True))
                header = [str(h).strip() if h is not None else None for h in rows[0]]
                return tabs[_key(n)], header, [dict(zip(header, r)) for r in rows[1:] if any(c is not None for c in r)]
        if required:
            raise ValueError(f"tab {names[0]!r} not found; tabs: {', '.join(wb.sheetnames)}")
        return None, [], []

    out: dict[str, list[dict]] = {}
    tab, header, rows = sheet("Admin divisions")
    c = _cols(header, ["country", "region", "region_code", "subregion", "subregion_code", "subregion_type",
                       "language", "zone", "market_eur", "share"],
              ["country", "region", "subregion", "subregion_code", "language"], tab)
    areas, zones, states = [], [], {}
    for r in rows:
        cc = _iso(r.get(c["country"]))
        if not cc or not r.get(c["subregion_code"]):
            continue
        region = str(r.get(c["region"]) or "").strip()
        code = str(r[c["subregion_code"]]).strip().removesuffix(".0")
        state_id = f"{cc}-S-{_slug(str(r.get(c['region_code'])) if c.get('region_code') and r.get(c['region_code']) else region)}"
        states.setdefault(state_id, {"country": cc, "area_id": state_id, "parent_area_id": None, "level": "STATE",
                                     "admin_code": state_id, "area_name": region, "region_name": region,
                                     "actor_fields": json.dumps({"countryCode": cc, "state": region}), "priority": "100"})
        sub_name = str(r[c["subregion"]]).strip()
        lang = str(r.get(c["language"]) or "").replace(" ", "").lower() or None
        areas.append({"country": cc, "area_id": f"{cc}-{code}", "parent_area_id": state_id, "level": "SUBREGION",
                      "subregion_type": (str(r.get(c["subregion_type"])).upper()
                                         if c.get("subregion_type") and r.get(c["subregion_type"]) else None),
                      "admin_code": code, "area_name": sub_name, "region_name": region, "default_language": lang,
                      "actor_fields": json.dumps({"countryCode": cc, "state": region, "county": sub_name},
                                                 ensure_ascii=False),
                      "priority": "100"})
        zone = str(r.get(c["zone"]) or "").strip().upper() if c.get("zone") else ""
        if zone in ("ATTACK", "DEFEND", "ACTIVATE", "OPTIMIZE"):
            zones.append({"country": cc, "admin_level": "SUBREGION", "admin_code": code, "admin_name": sub_name,
                          "quadrant": zone, "market_eur": r.get(c["market_eur"]) if c.get("market_eur") else None,
                          "fluidra_share": r.get(c["share"]) if c.get("share") else None,
                          "model_version": path.stem})
    out["biz_cfg_search_area"] = list(states.values()) + areas
    out["biz_cfg_strategic_zone"] = zones

    tab, header, rows = sheet("Profiles & keywords", "Profiles and keywords", "Keywords")
    c = _cols(header, ["country", "language", "profile", "keyword", "preselected"], ["language", "keyword"], tab)
    out["biz_cfg_search_term"] = [{
        "country": _iso(r.get(c["country"])) if c.get("country") else None,
        "language": str(r[c["language"]]).strip().lower(),
        "profile": _slug(str(r.get(c["profile"]) or "POOL_SPECIALIST")).replace("POOL_AND_SPA", "POOL_SPA")
        if c.get("profile") else "POOL_SPECIALIST",
        "term": str(r[c["keyword"]]).strip(),
        "preselected": "1" if str(r.get(c.get("preselected", ""), "")).strip().lower() in ("1", "yes", "y", "x", "true")
        else "0", "priority": str(i + 1)} for i, r in enumerate(rows) if r.get(c["keyword"])]

    tab, header, rows = sheet("Google categories")
    c = _cols(header, ["country", "language", "profile", "category", "classification"], ["category", "classification"], tab)
    out["biz_cfg_category_rule"] = [{
        "country": _iso(r.get(c["country"])) if c.get("country") else None,
        "language": (str(r.get(c["language"])).strip().lower() or None) if c.get("language") and r.get(c["language"]) else None,
        "profile": None, "category": str(r[c["category"]]).strip(),
        "classification": str(r[c["classification"]]).strip().upper()} for r in rows if r.get(c["category"])]

    tab, header, rows = sheet("Name exclusions")
    c = _cols(header, ["country", "pattern", "match_type", "class", "reason"], ["pattern", "class", "reason"], tab)
    out["biz_cfg_title_rule"] = [{
        "country": _iso(r.get(c["country"])) if c.get("country") else None, "profile": None,
        "pattern": str(r[c["pattern"]]).strip(),
        "match_type": str(r.get(c["match_type"]) or "TOKEN").strip().upper() if c.get("match_type") else "TOKEN",
        "rule_class": str(r[c["class"]]).strip().upper(), "reason_code": _slug(str(r[c["reason"]]))}
        for r in rows if r.get(c["pattern"])]

    tab, header, rows = sheet("Pool words")
    c = _cols(header, ["language", "stem"], ["language", "stem"], tab)
    out["biz_cfg_pool_signal"] = [{"country": None, "language": str(r[c["language"]]).strip().lower(),
                                   "profile": "POOL_SPECIALIST", "stem": str(r[c["stem"]]).strip()}
                                  for r in rows if r.get(c["stem"]) and "to define" not in str(r[c["stem"]]).lower()]

    tab, header, rows = sheet("Brands")
    c = _cols(header, ["country", "brand", "owner", "group", "aliases", "note"], ["brand", "owner"], tab)
    out["biz_cfg_brand"] = [{
        "country": _iso(r.get(c["country"])) if c.get("country") and r.get(c["country"]) else None,
        "brand": str(r[c["brand"]]).strip(),
        "owner": "FLUIDRA" if "fluidra" in str(r[c["owner"]]).lower() else "COMPETITOR",
        "brand_group": str(r.get(c["group"]) or "").strip().removesuffix(".0") if c.get("group") else None,
        "aliases": "|".join(a.strip() for a in str(r.get(c["aliases"]) or "").split(",") if a.strip())
        if c.get("aliases") else None,
        "disambiguation_note": r.get(c["note"]) if c.get("note") else None} for r in rows if r.get(c["brand"])]

    tab, header, rows = sheet("Subregion → sales rep", "Subregion -> sales rep", "Subregion sales rep", required=False)
    if tab:
        c = _cols(header, ["country", "subregion_code", "first_name", "last_name"], ["country", "last_name"], tab)
        out["biz_cfg_sales_owner"] = [{
            "country": _iso(r.get(c["country"])),
            "subregion_code": str(r.get(c["subregion_code"]) or "").strip().removesuffix(".0") or None
            if c.get("subregion_code") else None,
            "owner_first_name": str(r.get(c["first_name"]) or "").strip() if c.get("first_name") else "",
            "owner_last_name": str(r[c["last_name"]]).strip()}
            for r in rows if r.get(c["last_name"]) and "to define" not in str(r[c["last_name"]]).lower()]
    return out


def load_excel(store, path: Path, version: str, loaded_by: str, overwrite: bool = False) -> dict[str, int]:
    raw = excel_to_rows(path)
    typed = {table: typed_rows(table, rows) for table, rows in raw.items()}
    return load(store, typed, version, loaded_by, path.name, overwrite)
