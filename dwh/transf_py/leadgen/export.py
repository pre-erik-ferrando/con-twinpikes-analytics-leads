"""The Salesforce export (DT.8.5, review DT.8): one export, before the call, of the ACCEPTED leads.

The columns are configuration (biz_cfg_export_column, target SALESFORCE): FIELD reads a field of the
lead (lgn_v_salesforce_export), CONSTANT writes a text, VALUE_MAP translates a field with
biz_cfg_value_map. A default fills a NULL; padding a value a target system requires (e.g. a mandatory
email) goes here, never in the data.

A lead is exported once. Each export has an export_id and a state GENERATED -> UPLOADED | FAILED; a lead
becomes Published only when its export is confirmed UPLOADED, and a FAILED export can be regenerated.
Output: one xlsx per country, one sheet, <country>_salesforce_upload_<date>.xlsx.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path


class ExportError(Exception):
    pass


def lead_code(country: str, business_id: int) -> str:
    """country-business_id (DT.8.3), to match the call results afterwards."""
    return f"{country}-{business_id}"


def _format(value, fmt: str):
    if value is None:
        return None
    if fmt == "BOOLEAN_TEXT":
        if isinstance(value, str):
            return value
        return "True" if bool(value) else "False"
    if fmt == "NUMBER":
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    if fmt == "DATE":
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return str(value)


def build_rows(leads: list[dict], columns: list[dict], value_maps: dict[str, dict[str, str]]) -> tuple[list[str], list[list]]:
    """leads: dicts with the fields of lgn_v_salesforce_export (plus owner_* and category_of_interest
    resolved by the caller). Returns (header, rows). Raises ExportError on a column pointing to a field
    the leads do not have (blocks the export, not the run: DT.2.4)."""
    if not columns:
        raise ExportError("no active export columns for SALESFORCE")
    available = set(leads[0]) if leads else set()
    header = [c["column_name"] for c in columns]
    for c in columns:
        field = c["source_value"] if c["source_kind"] == "FIELD" else c.get("source_field")
        if c["source_kind"] in ("FIELD", "VALUE_MAP") and leads and field not in available:
            raise ExportError(f"export column {c['column_name']!r} points to a missing field {field!r}")
    rows = []
    for lead in leads:
        row = []
        for c in columns:
            kind = c["source_kind"]
            if kind == "CONSTANT":
                v = c["source_value"]
            elif kind == "FIELD":
                v = lead.get(c["source_value"])
            else:
                raw = lead.get(c["source_field"])
                key = "" if raw is None else (str(int(raw)) if isinstance(raw, bool) else str(raw))
                v = value_maps.get(c["source_value"], {}).get(key)
            if v in (None, "") and c.get("default_value") is not None:
                v = c["default_value"]
            row.append(_format(v, c.get("format") or "TEXT"))
        rows.append(row)
    return header, rows


def missing_owner(leads: list[dict]) -> list[int]:
    """Leads with no Owner: the export is blocked until the subregion -> salesperson table (or a
    country default owner) covers them."""
    return [l["business_id"] for l in leads if not (l.get("owner_last_name") or l.get("owner_first_name"))]


def family_comments(reason: str | None, fluidra_brands: list[str]) -> str | None:
    """Why it is a lead, preceded by the Fluidra brands detected, if any [PROPUESTA]."""
    if fluidra_brands:
        prefix = "Fluidra brands: " + ", ".join(fluidra_brands) + ". "
        return prefix + (reason or "")
    return reason


def write_xlsx(header: list[str], rows: list[list], out_dir: Path, country: str, day: date | None = None) -> Path:
    from openpyxl import Workbook

    day = day or date.today()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{country}_salesforce_upload_{day:%Y%m%d}.xlsx"
    n = 1
    while path.exists():
        n += 1
        path = out_dir / f"{country}_salesforce_upload_{day:%Y%m%d}_{n}.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Leads"
    ws.append(header)
    for r in rows:
        ws.append(r)
    # Zip codes and phones stay text: Excel would drop leading zeros and the '+'.
    for col_idx, name in enumerate(header, 1):
        if name.lower() in ("zip code", "phone", "mobile", "lead code"):
            for cell in ws.iter_cols(min_col=col_idx, max_col=col_idx, min_row=2):
                for c in cell:
                    c.number_format = "@"
    wb.save(path)
    return path
