"""Retailer adapter interface plus shared structured-data extraction (JSON-LD / OpenGraph)."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

from ..pricing import parse_price


class ExtractionError(Exception):
    def __init__(self, category: str, message: str, retryable: bool = True):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass
class ParsedOffer:
    retailer: str
    url: str
    canonical_url: str
    external_id: str | None = None
    title: str | None = None
    image_url: str | None = None
    brand: str | None = None
    model: str | None = None
    gtin: str | None = None
    variant_label: str | None = None
    price: Decimal | None = None
    mrp: Decimal | None = None
    delivery_charge: Decimal | None = None
    availability: str = "unknown"
    currency: str = "INR"
    confidence: Decimal = Decimal("0")
    source: str = "none"
    specs: dict = field(default_factory=dict)


BLOCK_MARKERS = ("captcha", "robot check", "enter the characters you see", "access denied",
                 "unusual traffic", "are you a human", "verify you are human")


def looks_blocked(html: str, title: str = "") -> bool:
    low = (title + " " + html[:6000]).lower()
    return any(m in low for m in BLOCK_MARKERS)


def _jsonld_blocks(soup: BeautifulSoup):
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(s.string or s.get_text() or "")
        except (ValueError, TypeError):
            continue
        stack = [data]
        while stack:
            d = stack.pop()
            if isinstance(d, list):
                stack.extend(d)
            elif isinstance(d, dict):
                if "@graph" in d:
                    stack.extend(d["@graph"] if isinstance(d["@graph"], list) else [d["@graph"]])
                yield d


def _type_is(d: dict, name: str) -> bool:
    t = d.get("@type")
    return name in t if isinstance(t, list) else t == name


def _avail(v) -> str:
    v = str(v or "").lower()
    if "instock" in v or "limitedavailability" in v or "onlineonly" in v:
        return "in_stock"
    if "outofstock" in v or "soldout" in v or "discontinued" in v:
        return "out_of_stock"
    return "unknown"


class RetailerAdapter:
    key = ""
    name = ""
    domains: tuple[str, ...] = ()
    short_domains: tuple[str, ...] = ()
    live_verified = False  # True only once selectors were confirmed against the live site
    id_patterns: tuple[str, ...] = ()
    # CSS fallbacks, tried in order. Kept per-retailer so site changes are a one-file fix.
    title_selectors: tuple[str, ...] = ("h1",)
    price_selectors: tuple[str, ...] = ()
    mrp_selectors: tuple[str, ...] = ()

    def matches(self, host: str) -> bool:
        host = (host or "").lower()
        return any(host == d or host.endswith("." + d) for d in self.domains + self.short_domains)

    def external_id(self, url: str) -> str | None:
        for pat in self.id_patterns:
            m = re.search(pat, url)
            if m:
                return m.group(1)
        return None

    def canonical_url(self, url: str) -> str:
        p = urlparse(url)
        return urlunparse(("https", p.hostname.lower(), p.path.rstrip("/") or "/", "", "", ""))

    def search_url(self, query: str) -> str | None:
        return None

    # -- parsing -----------------------------------------------------------
    def parse(self, html: str, url: str) -> ParsedOffer:
        soup = BeautifulSoup(html, "lxml")
        title_tag = soup.title.get_text(strip=True) if soup.title else ""
        if looks_blocked(html, title_tag):
            raise ExtractionError("blocked", f"{self.name} presented an access challenge.", retryable=True)
        out = ParsedOffer(retailer=self.key, url=url, canonical_url=self.canonical_url(url),
                          external_id=self.external_id(url))
        self._from_jsonld(soup, out)
        self._from_meta(soup, out)
        self._from_selectors(soup, out)
        self.postprocess(soup, out)
        if out.price is not None and out.mrp is not None and out.price > out.mrp:
            out.mrp = None  # inconsistent MRP is dropped rather than shown
        if not out.title:
            raise ExtractionError("parse", "Product title not found on page.", retryable=False)
        return out

    def postprocess(self, soup: BeautifulSoup, out: ParsedOffer) -> None:
        pass

    def _from_jsonld(self, soup, out: ParsedOffer) -> None:
        for d in _jsonld_blocks(soup):
            if not _type_is(d, "Product"):
                continue
            out.title = out.title or d.get("name")
            img = d.get("image")
            out.image_url = out.image_url or (img[0] if isinstance(img, list) and img else img if isinstance(img, str) else None)
            b = d.get("brand")
            out.brand = out.brand or (b.get("name") if isinstance(b, dict) else b if isinstance(b, str) else None)
            out.model = out.model or d.get("model") or d.get("mpn") or d.get("sku")
            for k in ("gtin13", "gtin", "gtin12", "gtin14", "gtin8"):
                if d.get(k):
                    out.gtin = str(d[k]); break
            offers = d.get("offers")
            offers = offers if isinstance(offers, list) else [offers] if offers else []
            for o in offers:
                if not isinstance(o, dict):
                    continue
                if _type_is(o, "AggregateOffer"):
                    price = parse_price(o.get("lowPrice"))
                else:
                    price = parse_price(o.get("price") or (o.get("priceSpecification") or {}).get("price")
                                        if isinstance(o.get("priceSpecification"), dict) else o.get("price"))
                if price and out.price is None:
                    out.price = price
                    out.currency = (o.get("priceCurrency") or "INR")[:3]
                    out.availability = _avail(o.get("availability"))
                    out.source = "jsonld"
                    out.confidence = Decimal("0.95")
            if out.price is not None or out.title:
                return

    def _from_meta(self, soup, out: ParsedOffer) -> None:
        def meta(*names):
            for n in names:
                t = soup.find("meta", attrs={"property": n}) or soup.find("meta", attrs={"name": n})
                if t and t.get("content"):
                    return t["content"].strip()
        out.title = out.title or meta("og:title", "twitter:title")
        out.image_url = out.image_url or meta("og:image")
        if out.price is None:
            p = parse_price(meta("product:price:amount", "og:price:amount"))
            if p:
                out.price, out.source, out.confidence = p, "meta", Decimal("0.85")
                out.currency = (meta("product:price:currency", "og:price:currency") or "INR")[:3]

    def _from_selectors(self, soup, out: ParsedOffer) -> None:
        if not out.title:
            for sel in self.title_selectors:
                el = soup.select_one(sel)
                if el and el.get_text(strip=True):
                    out.title = el.get_text(" ", strip=True); break
        if out.price is None:
            for sel in self.price_selectors:
                el = soup.select_one(sel)
                p = parse_price(el.get_text(" ", strip=True)) if el else None
                if p:
                    out.price, out.source, out.confidence = p, "selector", Decimal("0.7")
                    break
        if out.mrp is None:
            for sel in self.mrp_selectors:
                el = soup.select_one(sel)
                p = parse_price(el.get_text(" ", strip=True)) if el else None
                if p:
                    out.mrp = p; break
