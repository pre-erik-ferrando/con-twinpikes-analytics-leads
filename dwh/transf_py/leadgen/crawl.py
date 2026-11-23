"""Recursive website crawl (DT.6.1) with the JavaScript fallback (DT.6.2).

- Starts at the normalized homepage; follows links only inside the same registrable domain (on site
  builders, the same subdomain).
- Download order: sitemap.xml (and the one robots.txt declares), then URLs matching
  biz_cfg_word_list PAGE_PRIORITY, then breadth-first from the homepage, up to the limits.
- HTML only; robots.txt respected; one request per second per domain; per-page timeout, retries with
  growing waits, a time cap per domain, abandon after N consecutive failures; up to 3 redirects (the
  final domain becomes the website's, the original is kept).
- Little text on the homepage or single-page-app marks -> the domain is rendered with Playwright
  (optional dependency) under stricter limits. Bot protection or captcha -> BLOCKED; never bypassed.

The HTTP layer is injectable (`fetch`), so the tests run without network.
"""
from __future__ import annotations

import time
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urldefrag, urlsplit

from bs4 import BeautifulSoup

from .content import Page, needs_rendering
from .normalize import registrable_domain

USER_AGENT = "PrenomicsLeadBot/1.0 (+business contact research; respects robots.txt)"
BLOCK_MARKERS = ("captcha", "cf-challenge", "attention required", "access denied", "are you a robot")


@dataclass
class Response:
    url: str                  # final URL after redirects
    status: int
    content_type: str
    text: str
    n_redirects: int = 0


Fetch = Callable[[str, float], Response]


@dataclass
class CrawlLimits:
    max_depth: int = 3
    max_pages: int = 50
    max_bytes: int = 10 * 1024 * 1024
    requests_per_second: float = 1.0
    page_timeout_s: float = 15
    max_retries: int = 2
    domain_time_cap_s: float = 300
    max_consecutive_failures: int = 5
    max_redirects: int = 3
    render_text_threshold: int = 500
    render_max_pages: int = 20
    render_page_timeout_s: float = 30

    @classmethod
    def from_config(cls, cfg) -> "CrawlLimits":
        p = cfg.param
        return cls(max_depth=p("crawl_max_depth", 3), max_pages=p("crawl_max_pages", 50),
                   max_bytes=int(p("crawl_max_bytes", 10)) * 1024 * 1024,
                   requests_per_second=p("crawl_requests_per_second", 1.0),
                   page_timeout_s=p("crawl_page_timeout_s", 15), max_retries=p("crawl_max_retries", 2),
                   domain_time_cap_s=p("crawl_domain_time_cap_s", 300),
                   max_consecutive_failures=p("crawl_max_consecutive_failures", 5),
                   max_redirects=p("crawl_max_redirects", 3),
                   render_text_threshold=p("render_text_threshold_chars", 500),
                   render_max_pages=p("render_max_pages", 20), render_page_timeout_s=p("render_page_timeout_s", 30))


@dataclass
class CrawlResult:
    domain: str
    start_url: str
    final_url: str | None
    final_domain: str | None
    status: str
    render_method: str
    pages: list[Page] = field(default_factory=list)
    n_bytes: int = 0
    statuses: dict[str, int] = field(default_factory=dict)


def requests_fetch(url: str, timeout: float) -> Response:
    import requests

    r = requests.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT}, allow_redirects=True)
    return Response(r.url, r.status_code, r.headers.get("Content-Type", ""), r.text, len(r.history))


def _links(base: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        out.append(urldefrag(urljoin(base, href))[0])
    return out


def _sitemap_urls(xml: str) -> list[str]:
    import warnings

    from bs4 import XMLParsedAsHTMLWarning

    with warnings.catch_warnings():       # the <loc> tags are found either way; no XML parser needed
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(xml, "html.parser")
    return [loc.get_text(strip=True) for loc in soup.find_all("loc")]


SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf", ".zip", ".mp4", ".mp3", ".mov", ".avi",
            ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".css", ".js", ".ico", ".woff", ".woff2")


class Crawler:
    def __init__(self, limits: CrawlLimits, priority_patterns: list[str], site_builders: set[str],
                 fetch: Fetch = requests_fetch, sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, renderer: Callable | None = None):
        self.lim, self.patterns, self.site_builders = limits, priority_patterns, site_builders
        self.fetch, self.sleep, self.clock, self.renderer = fetch, sleep, clock, renderer

    def _same_site(self, url: str, domain: str) -> bool:
        return registrable_domain(url, self.site_builders) == domain

    def _get(self, url: str, state: dict) -> Response | None:
        wait = 1.0 / self.lim.requests_per_second if self.lim.requests_per_second else 0
        for attempt in range(self.lim.max_retries + 1):
            since = self.clock() - state["last"]
            if since < wait:
                self.sleep(wait - since)
            state["last"] = self.clock()
            try:
                r = self.fetch(url, self.lim.page_timeout_s)
            except Exception:                     # noqa: BLE001 - any network error is a failure
                r = None
            if r is not None and r.status < 500:
                return r
            if attempt < self.lim.max_retries:
                self.sleep(2 ** attempt)
        return None

    def crawl(self, website: str) -> CrawlResult:
        start = website if "://" in website else f"https://{website}"
        domain = registrable_domain(start, self.site_builders) or ""
        res = CrawlResult(domain, start, None, None, "UNREACHABLE", "HTTP")
        t0, state = self.clock(), {"last": -1e9}

        home = self._get(start, state)
        if home is None and start.startswith("https://"):
            start = "http://" + start[len("https://"):]
            home = self._get(start, state)
        if home is None:
            return res
        if home.n_redirects > self.lim.max_redirects:
            res.status = "UNREACHABLE"
            return res
        res.final_url = home.url
        res.final_domain = registrable_domain(home.url, self.site_builders)
        site = res.final_domain or domain
        low = (home.text or "")[:5000].lower()
        if home.status in (401, 403, 429) or any(m in low for m in BLOCK_MARKERS):
            res.status = "BLOCKED"
            return res

        robots = urllib.robotparser.RobotFileParser()
        root = f"{urlsplit(home.url).scheme}://{urlsplit(home.url).netloc}"
        rob = self._get(root + "/robots.txt", state)
        robots.parse(rob.text.splitlines() if rob is not None and rob.status == 200 else [])
        if not robots.can_fetch(USER_AGENT, home.url):
            res.status = "ROBOTS_DISALLOWED"
            return res

        sitemaps = list(robots.site_maps() or []) or [root + "/sitemap.xml"]
        queue: deque[tuple[str, int]] = deque()
        seen = {home.url}
        priority, rest = [], []
        for sm in sitemaps[:2]:
            r = self._get(sm, state)
            if r is not None and r.status == 200 and "<loc>" in r.text:
                for u in _sitemap_urls(r.text)[:500]:
                    (priority if any(p in u.lower() for p in self.patterns) else rest).append(u)
        pages = [Page("", home.url, 0, home.text)]
        res.n_bytes = len(home.text.encode())
        for u in _links(home.url, home.text):
            (priority if any(p in u.lower() for p in self.patterns) else rest).append(u)
        for u in priority:
            queue.append((u, 1))
        for u in rest:
            queue.append((u, 1))

        failures, errors, limit_hit = 0, 0, False
        while queue:
            if (len(pages) >= self.lim.max_pages or self.clock() - t0 > self.lim.domain_time_cap_s
                    or res.n_bytes > self.lim.max_bytes):
                limit_hit = True
                break
            url, depth = queue.popleft()
            if url in seen or depth > self.lim.max_depth or not self._same_site(url, site):
                continue
            if url.lower().split("?")[0].endswith(SKIP_EXT) or not robots.can_fetch(USER_AGENT, url):
                continue
            seen.add(url)
            r = self._get(url, state)
            if r is None or r.status >= 400:
                failures, errors = failures + 1, errors + 1
                if failures >= self.lim.max_consecutive_failures:
                    break
                continue
            failures = 0
            if "html" not in (r.content_type or "html").lower():
                continue
            pages.append(Page("", r.url, depth, r.text))
            res.n_bytes += len(r.text.encode())
            for link in _links(r.url, r.text):
                if link not in seen:
                    queue.append((link, depth + 1))
        res.status = "PARTIAL_LIMIT" if limit_hit else ("PARTIAL_ERROR" if errors else "OK")

        if needs_rendering(home.text, self.lim.render_text_threshold):
            rendered = self.renderer(home.url, self.lim) if self.renderer else None
            if rendered:
                pages = rendered + [p for p in pages if p.url not in {x.url for x in rendered}]
                res.render_method = "MIXED" if len(pages) > len(rendered) else "BROWSER"
            elif all(needs_rendering(p.html, self.lim.render_text_threshold) for p in pages):
                res.status = "EMPTY"
        res.pages = pages
        return res


def playwright_renderer(url: str, lim: CrawlLimits) -> list[Page] | None:
    """Renders up to render_max_pages pages of the site in headless Chromium, blocking images, fonts
    and media. Returns None when Playwright is not installed (the domain then stays HTTP-only)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pages: list[Page] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(user_agent=USER_AGENT)
        ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in
                  ("image", "font", "media") else route.continue_())
        tab = ctx.new_page()
        queue, seen = deque([(url, 0)]), set()
        domain = registrable_domain(url)
        while queue and len(pages) < lim.render_max_pages:
            u, depth = queue.popleft()
            if u in seen or depth > lim.max_depth:
                continue
            seen.add(u)
            try:
                tab.goto(u, timeout=lim.render_page_timeout_s * 1000, wait_until="networkidle")
            except Exception:                     # noqa: BLE001
                continue
            html = tab.content()
            pages.append(Page("", tab.url, depth, html))
            for link in _links(tab.url, html):
                if registrable_domain(link) == domain and link not in seen:
                    queue.append((link, depth + 1))
        browser.close()
    return pages or None
