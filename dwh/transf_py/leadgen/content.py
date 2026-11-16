"""Preparing a crawled website for the model (DT.6.3).

- Signals extracted without the model, per page and before stripping the HTML: mailto/tel links,
  obfuscated emails, JSON-LD Organization/LocalBusiness, VAT/UID/FN/HRA/HRB/KBO/KvK patterns, cart or
  checkout links, logo and image alt text -> EXTRACTED SIGNALS.
- HTML to text without scripts, styles, markup or hidden text.
- Text repeated across pages (menus, footers, cookie banners) is kept once, where it first appears.
- Pages ordered by relevance: priority patterns first, then by depth.
- Budget: tokens per page and per domain plus a character cap; if not everything fits, the request
  says "Content: truncated (N of M pages)".
- Brand mentions searched without the model in everything crawled, with aliases: up to N snippets of
  M characters per brand in BRAND MENTIONS, with a reserved share of the budget.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from .normalize import normalize_text

CART_RE = re.compile(r"(warenkorb|cart|basket|checkout|kasse|panier|winkelwagen|carrello|/shop/|add-to-cart)", re.I)
VAT_RE = re.compile(r"\b(?:DE\s?\d{9}|ATU\s?\d{8}|CHE[-\s]?\d{3}\.?\d{3}\.?\d{3}(?:\s?(?:MWST|TVA|IVA))?"
                    r"|BE\s?0?\d{3}\.?\d{3}\.?\d{3}|NL\s?\d{9}B\d{2})\b")
REGISTER_RE = re.compile(r"\b(?:HR[AB]\s?\d{2,6}|FN\s?\d{1,6}\s?[a-z]|KvK[-\s:]*\d{8}"
                         r"|(?:KBO|BCE|Ondernemingsnummer|Numéro d'entreprise)[-\s:]*0?\d{3}\.?\d{3}\.?\d{3})\b", re.I)
OBFUSCATED_EMAIL_RE = re.compile(r"\b([\w.+-]+)\s*(?:\[at\]|\(at\)| at |\[ät\]|@)\s*([\w-]+)\s*(?:\[dot\]|\(dot\)| dot |\.)"
                                 r"\s*([a-z]{2,})\b", re.I)
SPA_MARKERS = re.compile(r'(<div id="(?:root|app|__next)"\s*>\s*</div>|enable javascript|javascript aktivieren|'
                         r'activez javascript)', re.I)


@dataclass
class Page:
    page_id: str            # P1, P2… in relevance order
    url: str
    depth: int
    html: str
    text: str = ""
    signals: dict = field(default_factory=dict)


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup(["script", "style", "noscript", "template", "svg", "iframe"]):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden", re.I)):
        tag.decompose()
    for tag in soup.find_all(attrs={"hidden": True}):
        tag.decompose()
    for tag in soup.find_all(attrs={"aria-hidden": "true"}):
        tag.decompose()
    lines = (" ".join(line.split()) for line in soup.get_text("\n").splitlines())
    return "\n".join(line for line in lines if line)


def needs_rendering(html: str, text_threshold: int) -> bool:
    """A homepage with little useful text, or with the marks of a single-page app."""
    return len(html_to_text(html)) < text_threshold or bool(SPA_MARKERS.search(html or ""))


def extract_signals(html: str) -> dict:
    soup = BeautifulSoup(html or "", "html.parser")
    out: dict[str, list[str]] = {"mailto": [], "tel": [], "json_ld": [], "vat": [], "register": [], "cart": [],
                                 "logo_alt": [], "obfuscated_email": []}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        low = href.lower()
        if low.startswith("mailto:"):
            out["mailto"].append(href[7:].split("?")[0].strip())
        elif low.startswith("tel:"):
            out["tel"].append(href[4:].strip())
        elif CART_RE.search(low):
            out["cart"].append(href)
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or "")
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(data, dict):
            items = data.get("@graph", [data])
        else:
            items = data if isinstance(data, list) else []
        for item in items:
            kind = str(item.get("@type", "")) if isinstance(item, dict) else ""
            # LocalBusiness subtypes (HomeAndConstructionBusiness, Store…) carry the same fields
            if kind == "Organization" or "Business" in kind or kind.endswith("Store"):
                keep = {k: item.get(k) for k in ("@type", "name", "legalName", "vatID", "taxID", "telephone",
                                                 "email", "address") if item.get(k)}
                out["json_ld"].append(json.dumps(keep, ensure_ascii=False))
    for img in soup.find_all("img", alt=True):
        alt = img["alt"].strip()
        if alt and len(alt) <= 80:
            out["logo_alt"].append(alt)
    text = html_to_text(html)
    out["vat"] = sorted(set(VAT_RE.findall(text)))
    out["register"] = sorted(set(m.group(0) for m in REGISTER_RE.finditer(text)))
    out["obfuscated_email"] = sorted({f"{a}@{b}.{c}" for a, b, c in OBFUSCATED_EMAIL_RE.findall(text)
                                      if "@" not in f"{a}{b}"})
    for k in ("mailto", "tel", "cart", "logo_alt"):
        out[k] = list(dict.fromkeys(out[k]))[:20]
    return {k: v for k, v in out.items() if v}


def order_pages(pages: list[Page], priority_patterns: list[str]) -> list[Page]:
    """Priority patterns first (in their order), then by depth, then URL. Renumbers P1..Pn."""
    def rank(p: Page):
        path = p.url.lower()
        pr = next((i for i, pat in enumerate(priority_patterns) if pat in path), len(priority_patterns))
        return (pr, p.depth, len(p.url), p.url)
    ordered = sorted(pages, key=rank)
    for i, p in enumerate(ordered, 1):
        p.page_id = f"P{i}"
    return ordered


def dedupe_lines(pages: list[Page]) -> None:
    """Keeps each repeated line (menus, footers, cookie banners) only where it first appears."""
    seen: set[str] = set()
    for p in pages:
        kept = []
        for line in p.text.splitlines():
            key = line.strip().lower()
            if len(key) > 3 and key in seen:
                continue
            seen.add(key)
            kept.append(line)
        p.text = "\n".join(kept)


def estimate_tokens(text: str) -> int:
    """~4 characters per token. The engine re-counts with the provider before sending (DT.9.5)."""
    return (len(text) + 3) // 4


@dataclass
class BrandMention:
    brand: str
    page_id: str
    snippet: str


def brand_mentions(pages: list[Page], brands: list[dict], per_brand: int, chars: int) -> list[BrandMention]:
    """Brand names and aliases found at word boundaries, up to `per_brand` snippets of `chars`."""
    out = []
    for b in brands:
        names = [b["brand"], *(b.get("aliases") or [])]
        pats = [re.compile(r"(?<![\w])" + re.escape(n) + r"(?![\w])", re.I) for n in names if n]
        found = 0
        for p in pages:
            for pat in pats:
                for m in pat.finditer(p.text):
                    if found >= per_brand:
                        break
                    half = max(0, (chars - len(m.group(0))) // 2)
                    snippet = " ".join(p.text[max(0, m.start() - half): m.end() + half].split())
                    out.append(BrandMention(b["brand"], p.page_id, snippet[:chars]))
                    found += 1
            if found >= per_brand:
                break
    return out


@dataclass
class PreparedContent:
    pages: list[Page]                  # included pages, text possibly cut to the per-page budget
    n_pages_total: int
    truncated: bool
    mentions: list[BrandMention]
    content_hash: str = ""

    @property
    def content_line(self) -> str:
        if self.truncated:
            return f"truncated ({len(self.pages)} of {self.n_pages_total} pages)"
        return "complete"


def prepare(pages: list[Page], priority_patterns: list[str], brands: list[dict], tokens_per_page: int,
            tokens_per_domain: int, char_cap: int, brand_reserve_ratio: float, snippets_per_brand: int,
            snippet_chars: int) -> PreparedContent:
    import hashlib

    for p in pages:
        p.signals = extract_signals(p.html)
        p.text = html_to_text(p.html)
    ordered = order_pages(pages, priority_patterns)
    dedupe_lines(ordered)
    mentions = brand_mentions(ordered, brands, snippets_per_brand, snippet_chars)
    budget = int(tokens_per_domain * (1 - brand_reserve_ratio))
    non_empty = [p for p in ordered if p.text.strip()]
    used_tokens, used_chars, kept, truncated = 0, 0, [], False
    for p in non_empty:
        text = p.text
        if estimate_tokens(text) > tokens_per_page:
            text, truncated = text[: tokens_per_page * 4], True
        cost = estimate_tokens(text)
        if used_tokens + cost > budget or used_chars + len(text) > char_cap:
            truncated = True
            room = min((budget - used_tokens) * 4, char_cap - used_chars)
            if room > 500:
                p.text = text[:room]
                kept.append(p)
            break
        p.text = text
        kept.append(p)
        used_tokens += cost
        used_chars += len(text)
    h = hashlib.sha256("\n".join(f"{p.url}\n{p.text}" for p in kept).encode()).hexdigest()[:16]
    return PreparedContent(kept, len(non_empty), truncated or len(kept) < len(non_empty), mentions, h)


def normalized_contains(haystack: str, needle: str) -> bool:
    """Normalized comparison used to check that a claimed value appears on its cited page."""
    n = normalize_text(needle)
    return bool(n) and n in normalize_text(haystack)
