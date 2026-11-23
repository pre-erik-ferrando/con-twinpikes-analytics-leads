"""Editable configuration (DT.2): resolution of the current row per natural key and typed access.

Rules (DT.2.1 and the review):
- insert-only; the current row of a key is the last by (inserted_at, updated_by) -> argMax;
- deleting a row is inserting it again with active = 0;
- only rows with inserted_at <= the run's snapshot count (DT.2.6);
- country NULL applies to every country; a row for the run's country with the same key wins;
- profile NULL applies to every profile; a row for the run's profile wins (review, DT.2).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from . import ddl
from .domain import SCORING_BUCKETS, SCORING_FACTORS


class ConfigError(Exception):
    """A configuration problem that blocks the run."""


def latest_by_key(rows: list[dict], key: tuple[str, ...], snapshot_at: datetime | None = None) -> list[dict]:
    """argMax by (inserted_at, updated_by) per natural key, then active = 1 only."""
    best: dict[tuple, dict] = {}
    for r in rows:
        if snapshot_at is not None and r.get("inserted_at") is not None and r["inserted_at"] > snapshot_at:
            continue
        k = tuple(r.get(c) for c in key)
        cur = best.get(k)
        if cur is None or (r.get("inserted_at"), r.get("updated_by") or "") > (cur.get("inserted_at"),
                                                                                cur.get("updated_by") or ""):
            best[k] = r
    return [r for r in best.values() if r.get("active", 1) in (1, True)]


def scoped(rows: list[dict], key: tuple[str, ...], country: str | None, profile: str | None = None) -> list[dict]:
    """Keeps rows that apply to (country, profile) and lets the most specific row of each key win:
    country+profile > country > profile > neither."""
    def applies(r):
        return (r.get("country") in (None, country)) and (r.get("profile") in (None, profile))

    def rank(r):
        return (r.get("country") is not None) * 2 + (r.get("profile") is not None)

    rest = tuple(c for c in key if c not in ("country", "profile"))
    best: dict[tuple, dict] = {}
    for r in rows:
        if not applies(r):
            continue
        k = tuple(r.get(c) for c in rest)
        if k not in best or rank(r) > rank(best[k]):
            best[k] = r
    return list(best.values())


def cast_parameter(value: str | None, value_type: str):
    if value is None or value == "":
        return None
    if value_type == "INT":
        return int(float(str(value).replace(",", ".")))
    if value_type == "FLOAT":
        return float(str(value).replace(",", "."))
    if value_type == "BOOL":
        return str(value).strip().lower() in ("1", "true", "yes", "y")
    if value_type == "LIST":
        return [v.strip() for v in str(value).split(",") if v.strip()]
    return str(value)


@dataclass
class Config:
    """The configuration of one run: one country and one profile, at one snapshot."""
    country: str
    profile: str | None
    tables: dict[str, list[dict]] = field(default_factory=dict)

    @classmethod
    def from_rows(cls, raw: dict[str, list[dict]], country: str, profile: str | None,
                  snapshot_at: datetime | None = None) -> "Config":
        tables = {}
        for name, contract in ddl.CONFIG_TABLES.items():
            rows = latest_by_key(raw.get(name, []), contract.key, snapshot_at)
            tables[name] = scoped(rows, contract.key, country, profile) if contract.scoped else rows
        return cls(country, profile, tables)

    def rows(self, table: str) -> list[dict]:
        return self.tables.get(table, [])

    # --- parameters ---------------------------------------------------------------------------
    def param(self, name: str, default=None):
        for r in self.rows("biz_cfg_run_parameter"):
            if r["parameter"] == name:
                v = cast_parameter(r.get("value"), r.get("value_type") or "STRING")
                return default if v is None else v
        return default

    # --- typed views used by the engine -------------------------------------------------------
    def category_rules(self, language: str | None = None) -> dict[str, str]:
        """category_normalized -> classification. Rows with a language only apply to that language;
        language=None (unknown) takes every row."""
        out = {}
        for r in sorted(self.rows("biz_cfg_category_rule"), key=lambda r: r.get("language") is not None):
            if language is None or r.get("language") in (None, language):
                out[r["category_normalized"]] = r["classification"]
        return out

    def title_rules(self) -> list[dict]:
        return self.rows("biz_cfg_title_rule")

    def pool_stems(self) -> list[str]:
        return sorted(set(self.words("POOL_SIGNAL")))

    def legal_forms(self) -> list[str]:
        return self.words("LEGAL_FORM")

    def stopwords(self) -> set[str]:
        return set(self.words("NAME_STOPWORD"))

    def shared_domains(self, kind: str | None = None) -> set[str]:
        return {r["value"] for r in self.rows("biz_cfg_word_list")
                if r["list_name"] == "SHARED_DOMAIN" and (kind is None or r.get("kind") == kind)}

    def words(self, list_name: str) -> list[str]:
        """The values of one list of biz_cfg_word_list (NAME_STOPWORD, LEGAL_FORM, POOL_SIGNAL…)."""
        return [r["value"] for r in self.rows("biz_cfg_word_list") if r["list_name"] == list_name]

    def page_patterns(self) -> list[str]:
        """URL path fragments crawled first (PAGE_PRIORITY), by priority (100 when it has none)."""
        rows = [r for r in self.rows("biz_cfg_word_list") if r["list_name"] == "PAGE_PRIORITY"]
        return [r["value"] for r in sorted(rows, key=lambda r: (r.get("priority") if r.get("priority") is not None
                                                                 else 100, r["value"]))]

    def brands(self, owner: str) -> list[dict]:
        """Brands of the run's country if it has its own list, else the common list (country NULL)."""
        rows = [r for r in self.rows("biz_cfg_brand") if r["owner"] == owner]
        own = [r for r in rows if r.get("country") == self.country]
        return sorted(own or [r for r in rows if r.get("country") is None], key=lambda r: r["brand"])

    def matching_rule(self, signal: str) -> dict | None:
        return next((r for r in self.rows("biz_cfg_matching_rule") if r["signal"] == signal), None)

    def scoring_points(self) -> dict[tuple[str, str], float]:
        return {(r["factor"], r["bucket_code"]): float(r["points"]) for r in self.rows("biz_cfg_scoring_rule")}

    def rating_bands(self) -> list[tuple[str, float]]:
        """[(rating, min_score)] from the highest threshold down."""
        return sorted(((r["rating"], float(r["min_score"])) for r in self.rows("biz_cfg_rating_band")),
                      key=lambda x: -x[1])

    def strategic_zones(self) -> dict[str, str]:
        return {r["admin_code"]: r["quadrant"] for r in self.rows("biz_cfg_strategic_zone")}

    def search_areas(self) -> dict[str, dict]:
        return {r["area_id"]: r for r in self.rows("biz_cfg_search_area")}

    def search_terms(self) -> list[dict]:
        return sorted(self.rows("biz_cfg_search_term"), key=lambda r: (r.get("priority") or 0, r["term"]))

    def value_map(self, name: str) -> dict[str, str]:
        return {r["source_value"]: r["target_value"] for r in self.rows("biz_cfg_value_map") if r["map_name"] == name}

    def export_columns(self, target: str) -> list[dict]:
        return sorted((r for r in self.rows("biz_cfg_export_column") if r["target"] == target),
                      key=lambda r: r["column_order"])

    def source_columns(self, source_system: str) -> list[dict]:
        return [r for r in self.rows("biz_cfg_source_column") if r["source_system"] == source_system]

    def sales_owner(self, subregion_code: str | None) -> dict | None:
        """The salesperson of a subregion, else the country's default owner (subregion_code NULL)."""
        rows = self.rows("biz_cfg_sales_owner")
        exact = next((r for r in rows if subregion_code and r.get("subregion_code") == subregion_code), None)
        return exact or next((r for r in rows if r.get("subregion_code") is None), None)

    def category_of_interest(self, offering, company_type, pool_focus) -> str | None:
        for r in sorted(self.rows("biz_cfg_category_of_interest"), key=lambda r: r["rule_order"]):
            if all(r.get(f) in (None, v) for f, v in
                   (("offering", offering), ("company_type", company_type), ("pool_focus", pool_focus))):
                return r["category_of_interest"]
        return None

    def validation_reasons(self) -> dict[str, dict]:
        out = {}
        for r in self.rows("biz_cfg_validation_reason"):
            profiles = r.get("applies_to_profiles")
            if not profiles or self.profile in profiles:
                out[r["reason_code"]] = r
        return out


# --------------------------------------------------------------------------- precheck (DT.2.4 + review)
@dataclass
class PrecheckResult:
    blocking: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def precheck(cfg: Config, area_ids: list[str] | None = None, terms: list[str] | None = None,
             needs_scoring: bool = True) -> PrecheckResult:
    res = PrecheckResult()
    areas = cfg.search_areas()
    profiles = {r["profile_code"]: r for r in cfg.rows("biz_cfg_profile")}

    if cfg.profile is not None:
        if cfg.profile not in profiles:
            res.blocking.append(f"profile {cfg.profile} is not active in biz_cfg_profile")
        if not cfg.search_terms():
            res.blocking.append(f"profile {cfg.profile} has no active search terms in {cfg.country}")
        if not cfg.rows("biz_cfg_category_rule"):
            res.blocking.append(f"profile {cfg.profile} has no active Google category rules")
        if not cfg.title_rules():
            res.blocking.append(f"profile {cfg.profile} has no active title rules")

    if area_ids is not None:
        if not area_ids:
            res.blocking.append("no area selected")
        zones = cfg.strategic_zones()
        for a in area_ids:
            area = areas.get(a)
            if area is None:
                res.blocking.append(f"area {a} is not active in biz_cfg_search_area for {cfg.country}")
                continue
            if area.get("level") == "SUBREGION":
                if not area.get("default_language"):
                    res.blocking.append(f"subregion {a} has no default_language")
                if area.get("admin_code") not in zones:
                    res.warnings.append(f"subregion {a} has no quadrant (scores ZONE = NONE)")
    if terms is not None and not terms:
        res.blocking.append("no search term selected")

    if needs_scoring:
        points = cfg.scoring_points()
        for factor in SCORING_FACTORS:
            missing = [b for b in SCORING_BUCKETS[factor] if (factor, b) not in points]
            if missing:
                res.blocking.append(f"scoring factor {factor} lacks buckets {', '.join(missing)} in {cfg.country}")
        bands = dict(cfg.rating_bands())
        if set(bands) != {"HOT", "WARM", "COLD"}:
            res.blocking.append(f"rating bands incomplete in {cfg.country}: {sorted(bands)}")
        elif not bands["HOT"] > bands["WARM"] > bands["COLD"]:
            res.blocking.append(f"rating thresholds are not increasing in {cfg.country} (COLD < WARM < HOT)")
        if not cfg.brands("COMPETITOR"):
            res.warnings.append(f"no competitor brands for {cfg.country} nor a common list")
        elif not any(r.get("country") == cfg.country for r in cfg.rows("biz_cfg_brand")):
            res.warnings.append(f"{cfg.country} has no brand list of its own: the common list is used")
        for r in cfg.brands("COMPETITOR"):
            if r.get("brand_group") not in (1, 2, 3):
                res.warnings.append(f"competitor brand {r['brand']} has no group")

    for r in cfg.title_rules():
        if r.get("match_type") == "SUBSTRING" and len(r.get("pattern_normalized") or "") < 6:
            res.blocking.append(f"title rule {r['pattern']!r}: SUBSTRING needs six letters or more")
    try:
        ddl.validate_domains(cfg.tables)
    except ValueError as e:
        res.blocking.append(str(e))
    return res
