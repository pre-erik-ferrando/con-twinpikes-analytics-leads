"""Normalization (DT.4.1). The same functions are applied to leads, to website data and to customers,
so a lead and a customer compare on identical keys. The original value is always kept next to the
normalized one by the caller.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

import phonenumbers

from .domain import POSTAL_CODE_DIGITS

# DT.1.1.2: no sentinel values inside a data type. These become None on load.
SENTINELS = {"", "no website", "no web", "not checked", "#n/a", "n/a", "xxx", "not_applicable", "unknown",
             "none", "null", "-"}


def none_if_sentinel(value):
    if value is None:
        return None
    if isinstance(value, float) and value != value:   # NaN from pandas
        return None
    text = str(value).strip()
    return None if text.lower() in SENTINELS else text


# --------------------------------------------------------------------------- phone
def normalize_phone(raw, country: str | None) -> tuple[str | None, str | None]:
    """E.164 and the line type ('MOBILE', 'FIXED', 'OTHER') with the lead's country as default region."""
    raw = none_if_sentinel(raw)
    if raw is None:
        return None, None
    try:
        num = phonenumbers.parse(raw, country)
    except phonenumbers.NumberParseException:
        return None, None
    if not phonenumbers.is_valid_number(num):
        return None, None
    kind = phonenumbers.number_type(num)
    if kind == phonenumbers.PhoneNumberType.MOBILE:
        line = "MOBILE"
    elif kind in (phonenumbers.PhoneNumberType.FIXED_LINE, phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE):
        line = "FIXED"
    else:
        line = "OTHER"
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164), line


# --------------------------------------------------------------------------- web
# Second-level public suffixes that occur in our markets. A full public suffix list is not needed:
# every other TLD in DACH/Benelux registers directly under the TLD.
_SECOND_LEVEL = {"co.at", "or.at", "gv.at", "ac.at", "co.uk", "org.uk", "com.de"}


def url_host(url) -> str | None:
    url = none_if_sentinel(url)
    if url is None:
        return None
    if "://" not in url:
        url = "http://" + url
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not host or "." not in host:
        return None
    host = host.lower().rstrip(".")
    return host[4:] if host.startswith("www.") else host


def registrable_domain(url, site_builders: set[str] | frozenset = frozenset()) -> str | None:
    """Registrable domain, lower case, without protocol, www, parameters or fragment. On a site builder
    (biz_cfg_word_list SHARED_DOMAIN, kind SITE_BUILDER) the whole subdomain counts: foo.wixsite.com stays."""
    host = url_host(url)
    if host is None:
        return None
    labels = host.split(".")
    n = 3 if ".".join(labels[-2:]) in _SECOND_LEVEL else 2
    base = ".".join(labels[-n:])
    if base in site_builders and len(labels) > n:
        return ".".join(labels[-(n + 1):])
    return base


def email_domain(email) -> str | None:
    email = none_if_sentinel(email)
    if email is None or "@" not in email:
        return None
    return email.rsplit("@", 1)[1].strip().lower() or None


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def normalize_email(raw) -> str | None:
    raw = none_if_sentinel(raw)
    if raw is None:
        return None
    raw = raw.strip().removeprefix("mailto:").strip().lower()
    return raw if EMAIL_RE.match(raw) else None


# --------------------------------------------------------------------------- names
def strip_diacritics(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


_UMLAUT_AE = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_UMLAUT_A = str.maketrans({"ä": "a", "ö": "o", "ü": "u", "ß": "ss"})
_DOTTED_LETTERS = re.compile(r"\b(?:[a-z]\.){2,}|\b(?:[a-z]\.[a-z])\b")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
# Ignored in the exact key so "Peeters en Zonen" and "Peeters & Zonen" coincide (review DT.4.1).
CONJUNCTIONS = ("und", "et", "en", "e")


def _join_dotted(text: str) -> str:
    """'s.r.l.' -> 'srl', 'e.u.' -> 'eu': single letters separated by dots are joined before the
    symbols are removed."""
    return _DOTTED_LETTERS.sub(lambda m: m.group(0).replace(".", ""), text)


def _base(text: str, umlauts) -> str:
    text = text.lower().translate(umlauts)
    text = strip_diacritics(text)
    text = text.replace("&", " und ")
    text = _join_dotted(text)
    return " ".join(_NON_ALNUM.sub(" ", text).split())


def _remove_phrases(text: str, phrases) -> str:
    """Removes whole-token phrases in any position, longest first."""
    padded = f" {text} "
    for phrase in sorted(phrases, key=len, reverse=True):
        if phrase:
            padded = padded.replace(f" {phrase} ", " ")
            # the same phrase may occur twice ("gmbh ... gmbh")
            padded = padded.replace(f" {phrase} ", " ")
    return " ".join(padded.split())


def normalize_text(text) -> str:
    """Lower case, umlauts to ae/oe/ue, no diacritics, & -> und, symbols to spaces, spaces collapsed.
    Used for titles, owner descriptions, title-rule patterns and pool signals."""
    text = none_if_sentinel(text)
    return _base(text, _UMLAUT_AE) if text else ""


def name_keys(name, legal_forms=()) -> tuple[str, str]:
    """Both name keys (DT.4.1): umlauts to ae/oe/ue in the first, to a/o/u in the second; ß -> ss in
    both. Legal forms (biz_cfg_word_list LEGAL_FORM, already normalized) are removed in any position. Two names
    match if either key matches."""
    name = none_if_sentinel(name)
    if not name:
        return "", ""
    keys = []
    for table in (_UMLAUT_AE, _UMLAUT_A):
        keys.append(_remove_phrases(_base(name, table), legal_forms))
    return keys[0], keys[1]


def exact_key(key: str) -> str:
    """The key used for exact name comparison: name key without conjunctions."""
    return " ".join(t for t in key.split() if t not in CONJUNCTIONS)


def normalize_category(category) -> str:
    """Google category: lower case, no diacritics, spaces collapsed (DT.4.4.2)."""
    category = none_if_sentinel(category)
    if not category:
        return ""
    return " ".join(strip_diacritics(category.lower()).split())


def distinctive_tokens(key: str, stopwords) -> set[str]:
    """Words of a name key that identify the business (not generic sector words, B.12)."""
    stop = set(stopwords)
    return {t for t in key.split() if t not in stop and t not in CONJUNCTIONS and len(t) > 1}


# --------------------------------------------------------------------------- postal code, VAT, registers
_NL_POSTAL = re.compile(r"^(\d{4})\s*([A-Za-z]{2})$")


def normalize_postal_code(raw, country: str | None) -> str | None:
    """Text with leading zeros: 5 digits in DE, 4 in AT, CH and BE; NL '1234AB'. Anything else is None
    (e.g. 'XXX')."""
    raw = none_if_sentinel(raw)
    if raw is None:
        return None
    raw = raw.strip()
    if country == "NL":
        m = _NL_POSTAL.match(raw)
        return f"{m.group(1)}{m.group(2).upper()}" if m else None
    if raw.endswith(".0"):            # a numeric column read as float by Excel
        raw = raw[:-2]
    raw = re.sub(r"^(?:[A-Z]{1,2}-)", "", raw.upper())   # 'A-5020', 'CH-8000', 'D-80331'
    if not raw.isdigit():
        return None
    width = POSTAL_CODE_DIGITS.get(country or "")
    if width is None:
        return raw
    if len(raw) > width:
        return None
    return raw.zfill(width)


_VAT_FORMATS = {
    "DE": re.compile(r"^DE\d{9}$"),
    "AT": re.compile(r"^ATU\d{8}$"),
    "CH": re.compile(r"^CHE\d{9}$"),
    "BE": re.compile(r"^BE[01]\d{9}$"),
    "NL": re.compile(r"^NL\d{9}B\d{2}$"),
}
_VAT_SUFFIXES = re.compile(r"(MWST|TVA|IVA|HR|MVA)$")


def normalize_vat(raw, country: str | None = None) -> str | None:
    """Country prefix + digits (and the U of Austria, the B of the Netherlands), no separators, no
    suffixes (MWST, TVA, IVA). Returns None if the result has no valid format for its prefix.
    A Steuernummer has no country prefix, so it never passes."""
    raw = none_if_sentinel(raw)
    if raw is None:
        return None
    text = re.sub(r"[\s.\-/]", "", raw.upper())
    text = _VAT_SUFFIXES.sub("", text)
    if re.fullmatch(r"BE\d{9}", text):          # old 9-digit Belgian numbers
        text = "BE0" + text[2:]
    prefix = text[:2]
    fmt = _VAT_FORMATS.get(prefix)
    if fmt is None or not fmt.match(text):
        return None
    # A foreign VAT on a website is kept as is (`country` is unused on purpose): the comparison with
    # customers is exact on the normalized value, whatever its country.
    return text


def vat_format_ok(vat: str | None) -> bool:
    return bool(vat) and bool(_VAT_FORMATS.get(vat[:2], re.compile("^$")).match(vat))


def normalize_register(raw, register_type: str | None, country: str | None,
                       register_court: str | None = None) -> str | None:
    """Commercial register entry as a comparable key. DE: 'HRB12345@court' (type, number and court
    all needed); AT: 'FN123456a'; CH: the UID (as VAT CHE…); BE: KBO/BCE 10 digits; NL: KvK 8 digits."""
    raw = none_if_sentinel(raw)
    if raw is None:
        return None
    text = re.sub(r"[\s.\-/]", "", raw.upper())
    if country == "BE":
        digits = re.sub(r"\D", "", text)
        return f"KBO{digits.zfill(10)}" if len(digits) in (9, 10) else None
    if country == "NL":
        digits = re.sub(r"\D", "", text)
        return f"KVK{digits}" if len(digits) == 8 else None
    if country == "AT":
        m = re.search(r"(?:FN)?(\d{1,6}[A-Z])", text)
        return f"FN{m.group(1)}" if m else None
    if country == "CH":
        return normalize_vat(raw)
    if country == "DE":
        m = re.search(r"(HRA|HRB|GNR|PR|VR)(\d+)", text)
        if not m:
            return None
        court = normalize_text(register_court)
        if not court:
            return None        # in Germany the number only identifies a company together with its court
        rtype = m.group(1) if register_type is None else register_type.upper().strip()
        return f"{rtype}{m.group(2)}@{court.replace('amtsgericht ', '')}"
    return text or None
