"""Page fetching. HTTP first, Chromium (Playwright) only when needed.

No stealth/anti-detection tricks and no proxy rotation: if a retailer blocks us we report it.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from .retailers.base import ExtractionError
from .security import UA, host_allowed

log = logging.getLogger("priceradar.fetch")


@dataclass
class FetchResult:
    html: str
    final_url: str
    status: int
    via: str


class Fetcher:
    """Interface; tests substitute a fake."""
    def fetch(self, url: str, *, render: bool = False) -> FetchResult:  # pragma: no cover
        raise NotImplementedError


class RobotsCache:
    def __init__(self):
        self._cache: dict[str, RobotFileParser | None] = {}
        self._lock = threading.Lock()

    def allowed(self, url: str) -> bool:
        p = urlparse(url)
        origin = f"{p.scheme}://{p.hostname}"
        with self._lock:
            rp = self._cache.get(origin)
            if origin not in self._cache:
                rp = RobotFileParser()
                try:
                    r = requests.get(origin + "/robots.txt", timeout=8, headers={"User-Agent": UA})
                    rp.parse(r.text.splitlines()) if r.status_code == 200 else rp.allow_all  # noqa
                    if r.status_code != 200:
                        rp = None
                except requests.RequestException:
                    rp = None
                self._cache[origin] = rp
        return True if rp is None else rp.can_fetch(UA, url)


_tls = threading.local()


class LiveFetcher(Fetcher):
    def __init__(self, allowed_domains: set[str], timeout: int = 30, use_playwright: bool = True,
                 respect_robots: bool = True):
        self.allowed = allowed_domains
        self.timeout = timeout
        self.use_playwright = use_playwright
        self.robots = RobotsCache() if respect_robots else None
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})

    def fetch(self, url: str, *, render: bool = False) -> FetchResult:
        if not host_allowed(urlparse(url).hostname or "", self.allowed):
            raise ExtractionError("unsupported", "Host is not allow-listed.", retryable=False)
        if self.robots and not self.robots.allowed(url):
            raise ExtractionError("blocked", "robots.txt disallows automated access to this page.", retryable=False)
        if not render:
            try:
                r = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            except requests.Timeout as e:
                raise ExtractionError("timeout", "Request timed out.") from e
            except requests.RequestException as e:
                raise ExtractionError("network", f"Network error: {type(e).__name__}") from e
            if not host_allowed(urlparse(r.url).hostname or "", self.allowed):
                raise ExtractionError("unsupported", "Redirected to a non-allow-listed host.", retryable=False)
            if r.status_code in (403, 429, 503):
                raise ExtractionError("blocked", f"Retailer returned HTTP {r.status_code}.")
            if r.status_code == 404:
                raise ExtractionError("not_found", "Product page not found.", retryable=False)
            return FetchResult(r.text, r.url, r.status_code, "http")
        return self._render(url)

    def _browser(self):
        from playwright.sync_api import sync_playwright
        b = getattr(_tls, "browser", None)
        if b is None or not b.is_connected():
            _tls.pw = sync_playwright().start()
            _tls.browser = _tls.pw.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        return _tls.browser

    def _render(self, url: str) -> FetchResult:
        if not self.use_playwright:
            raise ExtractionError("unsupported", "Browser rendering disabled.", retryable=False)
        ctx = None
        try:
            ctx = self._browser().new_context(user_agent=UA, locale="en-IN", timezone_id="Asia/Kolkata")
            page = ctx.new_page()
            allowed = self.allowed
            # Only same-retailer documents may be navigated to (SSRF guard for redirects).
            page.route("**/*", lambda route: route.continue_()
                       if route.request.resource_type != "document"
                       or host_allowed(urlparse(route.request.url).hostname or "", allowed)
                       else route.abort())
            resp = page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            page.wait_for_timeout(1500)
            html, final = page.content(), page.url
            status = resp.status if resp else 0
            if not host_allowed(urlparse(final).hostname or "", allowed):
                raise ExtractionError("unsupported", "Redirected to a non-allow-listed host.", retryable=False)
            if status in (403, 429):
                raise ExtractionError("blocked", f"Retailer returned HTTP {status}.")
            return FetchResult(html, final, status, "browser")
        except ExtractionError:
            raise
        except Exception as e:  # playwright errors
            name = type(e).__name__
            raise ExtractionError("timeout" if "Timeout" in name else "network", f"Browser error: {name}") from e
        finally:
            if ctx:
                try: ctx.close()
                except Exception: pass


def close_thread_browser():
    b = getattr(_tls, "browser", None)
    if b:
        try: b.close(); _tls.pw.stop()
        except Exception: pass
        _tls.browser = None
