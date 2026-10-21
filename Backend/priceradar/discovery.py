"""Provider-based discovery of the same product at other retailers.

Implemented providers:
  * RetailerSearchProvider – calls each retailer's search URL, extracts product-link
    candidates from HTML (static) and falls back to a JS-rendered page when the static
    fetch yields nothing. Results are only CANDIDATES; every candidate is fetched, parsed
    and must pass the matching gate before it is shown as verified.
  * Manual offers – the user pastes the other retailer's URL (see API POST /products/<id>/offers).

Strategy for JS-heavy Indian retailers
---------------------------------------
Flipkart, Croma, Reliance Digital and Vijay Sales all render their search results via
client-side JS. A plain HTTP fetch of the search URL often returns skeletal HTML with no
product links.  We work around this in two ways:

  1. JSON/API shortcut – several retailers expose a JSON search endpoint; we try that first
     (fast, no browser).
  2. Browser fallback – if the static fetch yields < ``MIN_STATIC_LINKS`` product links we
     re-fetch with Playwright and try again.

NOT implemented: aggregator/affiliate-feed providers (Google Shopping, Admitad, Cuelinks …).
Implement ``DiscoveryProvider`` and register it in ``default_providers`` once you have
credentials.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from urllib.parse import quote_plus, urljoin, urlparse

from bs4 import BeautifulSoup

from .fetcher import Fetcher
from .retailers import ADAPTERS
from .retailers.base import ExtractionError

log = logging.getLogger("priceradar.discovery")

# If a static HTML fetch yields fewer candidate links than this we try a browser render.
MIN_STATIC_LINKS = 2
# Candidates collected per retailer during discovery (more = better recall, slower).
DEFAULT_PER_RETAILER = 8


@dataclass
class Candidate:
    retailer_key: str
    url: str
    provider: str


class DiscoveryProvider:
    name = "base"

    def discover(self, query: str, exclude_retailer: str) -> tuple[list[Candidate], list[str]]:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Retailer-specific JSON/API search helpers
# These return raw product-URL strings; empty list means "not supported / failed".
# ---------------------------------------------------------------------------

def _flipkart_api_links(fetcher: Fetcher, query: str, limit: int) -> list[str]:
    """
    Flipkart's internal search API – returns JSON.
    Endpoint used by the web app itself; not officially documented but stable.
    """
    api = (
        f"https://www.flipkart.com/search?q={quote_plus(query)}"
        f"&otracker=search&as=on&as-show=on&as-pos=1"
        # Ask for JSON by setting the af header used by Flipkart's SPA
    )
    try:
        res = fetcher.fetch(api, render=False)
        # Flipkart sometimes returns JSON directly for API-style requests
        try:
            data = json.loads(res.html)
            links: list[str] = []
            _extract_fk_json(data, links, limit)
            if links:
                return links
        except (ValueError, TypeError):
            pass
        # Otherwise parse the HTML for product links
        return _extract_links_from_html(res.html, res.final_url, "flipkart.com", limit)
    except ExtractionError:
        return []


def _extract_fk_json(obj, out: list[str], limit: int) -> None:
    """Recursively walk Flipkart JSON looking for product URLs."""
    if len(out) >= limit:
        return
    if isinstance(obj, dict):
        # Common key patterns in Flipkart's API
        for key in ("productUrl", "url", "href", "landingUrl"):
            v = obj.get(key)
            if isinstance(v, str) and "/p/" in v:
                full = v if v.startswith("http") else "https://www.flipkart.com" + v
                out.append(full)
                if len(out) >= limit:
                    return
        for v in obj.values():
            _extract_fk_json(v, out, limit)
    elif isinstance(obj, list):
        for item in obj:
            _extract_fk_json(item, out, limit)


def _croma_api_links(fetcher: Fetcher, query: str, limit: int) -> list[str]:
    """Croma has a JSON search endpoint under /api."""
    try:
        api = f"https://www.croma.com/api/2.0/rest/v2/cromasite/products/search?query={quote_plus(query)}&pageSize={limit}&currentPage=0"
        res = fetcher.fetch(api, render=False)
        data = json.loads(res.html)
        links: list[str] = []
        products = data.get("products") or []
        for p in products:
            slug = p.get("url") or p.get("slug") or ""
            if slug:
                url = slug if slug.startswith("http") else f"https://www.croma.com{slug}"
                links.append(url)
                if len(links) >= limit:
                    break
        return links
    except Exception:
        return []


def _reliance_api_links(fetcher: Fetcher, query: str, limit: int) -> list[str]:
    """Reliance Digital JSON search."""
    try:
        api = f"https://www.reliancedigital.in/silcs/v1.0/search?q={quote_plus(query)}&page=1&size={limit}"
        res = fetcher.fetch(api, render=False)
        data = json.loads(res.html)
        links: list[str] = []
        items = data.get("productListItems") or data.get("products") or data.get("data", {}).get("products", [])
        for p in items:
            slug = p.get("url") or p.get("seoUrl") or p.get("slug") or ""
            if slug:
                url = slug if slug.startswith("http") else f"https://www.reliancedigital.in{slug}"
                links.append(url)
                if len(links) >= limit:
                    break
        return links
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Generic HTML link extractor
# ---------------------------------------------------------------------------

def _extract_links_from_html(html: str, base_url: str, domain_hint: str, limit: int) -> list[str]:
    """
    Pull product page links from raw HTML.  We look for <a href> tags whose URL:
      * resolves to the same domain as domain_hint
      * contains an ID-like path segment that the adapter's external_id() recognises

    Returns a de-duplicated list, capped at ``limit``.
    """
    from .retailers import ADAPTERS
    # Find the adapter for this domain
    ad = next((a for a in ADAPTERS if domain_hint in " ".join(a.domains)), None)
    soup = BeautifulSoup(html, "lxml")
    seen: set[str] = set()
    links: list[str] = []
    for tag in soup.find_all("a", href=True):
        href = urljoin(base_url, tag["href"])
        p = urlparse(href)
        host = (p.hostname or "").lower()
        if not (domain_hint in host):
            continue
        # If we know the adapter, require a recognisable product ID in the URL
        if ad and not ad.external_id(href):
            continue
        canon = href.split("?")[0].split("#")[0].rstrip("/")
        if canon in seen:
            continue
        seen.add(canon)
        links.append(href)
        if len(links) >= limit:
            break
    return links


# ---------------------------------------------------------------------------
# Main provider
# ---------------------------------------------------------------------------

class RetailerSearchProvider(DiscoveryProvider):
    name = "retailer_search"

    # Retailer keys that have a working JSON/API shortcut
    _API_SHORTCUTS: dict[str, callable] = {}   # populated after class definition

    def __init__(self, fetcher: Fetcher, per_retailer: int = DEFAULT_PER_RETAILER):
        self.fetcher = fetcher
        self.per_retailer = per_retailer

    def discover(self, query: str, exclude_retailer: str) -> tuple[list[Candidate], list[str]]:
        found: list[Candidate] = []
        warnings: list[str] = []
        for ad in ADAPTERS:
            if ad.key == exclude_retailer:
                continue
            surl = ad.search_url(query)
            if not surl:
                continue
            links = self._search_links(ad, surl, query)
            if not links:
                warnings.append(f"{ad.name}: no product links found in search results (page may be JS-only or blocked).")
                continue
            seen: set[str] = set()
            for href in links:
                if not ad.external_id(href):
                    continue
                canon = ad.canonical_url(href)
                if canon in seen:
                    continue
                seen.add(canon)
                found.append(Candidate(ad.key, canon, self.name))
                if len(seen) >= self.per_retailer:
                    break
            if not seen:
                warnings.append(f"{ad.name}: no valid product URLs after filtering.")
        return found, warnings

    def _search_links(self, ad, surl: str, query: str) -> list[str]:
        """
        Try:
          1. Retailer-specific JSON/API shortcut (fast, no browser).
          2. Plain HTTP fetch of the HTML search page.
          3. Browser-rendered fetch (Playwright) if static HTML has too few links.
        """
        limit = self.per_retailer * 3   # fetch more than needed; some will be rejected

        # 1. JSON/API shortcut
        shortcut = _API_SHORTCUTS.get(ad.key)
        if shortcut:
            try:
                links = shortcut(self.fetcher, query, limit)
                if links:
                    log.debug("discovery: %s API shortcut returned %d links", ad.key, len(links))
                    return links
            except Exception as e:
                log.debug("discovery: %s API shortcut failed: %s", ad.key, e)

        # 2. Plain HTTP fetch
        links: list[str] = []
        try:
            res = self.fetcher.fetch(surl, render=False)
            links = _extract_links_from_html(res.html, res.final_url, ad.domains[0], limit)
            log.debug("discovery: %s static fetch returned %d links", ad.key, len(links))
        except ExtractionError as e:
            log.debug("discovery: %s static fetch failed: %s", ad.key, e.category)

        # 3. Browser fallback if static result is thin
        if len(links) < MIN_STATIC_LINKS:
            try:
                res = self.fetcher.fetch(surl, render=True)
                links = _extract_links_from_html(res.html, res.final_url, ad.domains[0], limit)
                log.debug("discovery: %s browser fetch returned %d links", ad.key, len(links))
            except ExtractionError as e:
                log.debug("discovery: %s browser fetch failed: %s", ad.key, e.category)

        return links


# Register JSON shortcuts after class definition to avoid forward-reference issues
_API_SHORTCUTS: dict[str, callable] = {
    "flipkart":       _flipkart_api_links,
    "croma":          _croma_api_links,
    "reliancedigital": _reliance_api_links,
}
RetailerSearchProvider._API_SHORTCUTS = _API_SHORTCUTS


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def build_query(brand: str | None, model_codes: set[str], title: str) -> str:
    """
    Build the best possible cross-retailer search query.

    Priority:
      1. If we have model codes, use ``brand + longest_model_code`` (most precise).
      2. Otherwise fall back to the first N meaningful words from the title,
         but always keep numbers, units (GB, TB, inch …) and known spec tokens.
    """
    if model_codes:
        code = sorted(model_codes, key=len, reverse=True)[0].upper()
        return f"{brand or ''} {code}".strip()

    # Title-based fallback — keep spec tokens (numbers + units, colour variants, etc.)
    # but drop generic marketing words that vary across retailers.
    _SKIP = {
        "with", "free", "buy", "online", "india", "latest", "new", "official",
        "warranty", "year", "genuine", "certified", "imported", "refurbished",
        "open", "box", "combo", "bundle", "pack", "kit", "set",
    }
    words = re.sub(r"[^A-Za-z0-9./ ]+", " ", title).split()
    kept: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in _SKIP:
            continue
        kept.append(w)
        # Stop after 10 meaningful words; enough context for search
        if len(kept) >= 10:
            break
    return " ".join(kept)


def default_providers(fetcher: Fetcher) -> list[DiscoveryProvider]:
    return [RetailerSearchProvider(fetcher)]
