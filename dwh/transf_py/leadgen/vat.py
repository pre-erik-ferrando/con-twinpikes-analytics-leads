"""VAT check (DT.5.6): VIES for EU numbers (DE, AT and the rest), the Swiss UID register for CHE numbers.

Only fills vat_check_status, never stops a run: VALID, NOT_FOUND (the register says it does not exist),
INVALID_FORMAT (the country format fails, no call made), UNAVAILABLE (the service failed after its
retries) and NOT_CHECKED (no VAT, or no checker). Retries with a growing wait (vies_max_retries) and at
most vies_max_requests_per_second calls, both services together. One call per number per checker.

The services are free and public: VIES REST (ec.europa.eu) and the UID register's public SOAP service
(uid-wse.admin.ch, ValidateVatNumber), not Zefix.
"""
from __future__ import annotations

import re
import time
from typing import Callable, Protocol

from .normalize import vat_format_ok

VIES_URL = "https://ec.europa.eu/taxation_customs/vies/rest-api/ms/{ms}/vat/{number}"
UID_URL = "https://www.uid-wse.admin.ch/V5.0/PublicServices.svc"
UID_ACTION = "http://www.uid.admin.ch/xmlns/uid-wse/IPublicServices/ValidateVatNumber"
UID_BODY = ('<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
            '<ValidateVatNumber xmlns="http://www.uid.admin.ch/xmlns/uid-wse"><vatNumber>{uid}</vatNumber>'
            '</ValidateVatNumber></s:Body></s:Envelope>')


class Unavailable(Exception):
    """The service did not give an answer (network, HTTP error, busy member state): retry."""


class VatChecker(Protocol):
    def check(self, vat: str | None) -> str: ...


# http(method, url, headers, body) -> (status code, text)
Http = Callable[[str, str, dict, str | None], tuple[int, str]]


def _requests_http(method: str, url: str, headers: dict, body: str | None) -> tuple[int, str]:
    import requests
    r = requests.request(method, url, headers=headers, data=body.encode() if body else None, timeout=20)
    return r.status_code, r.text


class PublicVatChecker:
    def __init__(self, max_retries: int = 3, max_per_second: float = 5, http: Http | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.max_retries, self.min_gap = max_retries, 1.0 / max_per_second if max_per_second else 0.0
        self.http, self.sleep, self.clock = http or _requests_http, sleep, clock
        self._last: float | None = None
        self._seen: dict[str, str] = {}

    @classmethod
    def from_config(cls, cfg, **kw) -> "PublicVatChecker":
        return cls(int(cfg.param("vies_max_retries", 3)), float(cfg.param("vies_max_requests_per_second", 5)), **kw)

    def check(self, vat: str | None) -> str:
        if not vat:
            return "NOT_CHECKED"
        if not vat_format_ok(vat):
            return "INVALID_FORMAT"
        if vat not in self._seen:
            self._seen[vat] = self._with_retries(vat)
        return self._seen[vat]

    def _with_retries(self, vat: str) -> str:
        for attempt in range(self.max_retries + 1):
            if attempt:
                self.sleep(2 ** (attempt - 1))                  # 1, 2, 4 s
            self._throttle()
            try:
                return self._uid(vat) if vat.startswith("CHE") else self._vies(vat)
            except Exception:                                   # Unavailable, or the HTTP client's errors
                continue
        return "UNAVAILABLE"

    def _throttle(self) -> None:
        now = self.clock()
        if self._last is not None and now - self._last < self.min_gap:
            self.sleep(self.min_gap - (now - self._last))
        self._last = self.clock()

    def _vies(self, vat: str) -> str:
        import json
        code, text = self.http("GET", VIES_URL.format(ms=vat[:2], number=vat[2:]), {"Accept": "application/json"}, None)
        if code != 200:
            raise Unavailable(f"VIES HTTP {code}")
        d = json.loads(text)
        err = d.get("userError")
        if d.get("isValid") is True or err == "VALID":
            return "VALID"
        if err == "INVALID":
            return "NOT_FOUND"
        if err == "INVALID_INPUT":
            return "INVALID_FORMAT"
        raise Unavailable(f"VIES {err}")                        # MS_UNAVAILABLE, TIMEOUT, *_MAX_CONCURRENT_REQ…

    def _uid(self, vat: str) -> str:
        digits = vat[3:]
        uid = f"CHE-{digits[:3]}.{digits[3:6]}.{digits[6:]}"
        code, text = self.http("POST", UID_URL, {"Content-Type": "text/xml; charset=utf-8",
                                                 "SOAPAction": f'"{UID_ACTION}"'}, UID_BODY.format(uid=uid))
        m = re.search(r"<ValidateVatNumberResult>\s*(true|false)\s*</ValidateVatNumberResult>", text or "")
        if code != 200 or not m:
            raise Unavailable(f"UID HTTP {code}")
        return "VALID" if m.group(1) == "true" else "NOT_FOUND"
