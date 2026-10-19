"""Concrete retailer adapters. Selector lists are best-effort fallbacks behind JSON-LD/meta parsing.

IMPORTANT: ``live_verified`` is False for every adapter in this release. Parsing is covered by
fixture tests only; confirm against live pages before relying on a retailer in production.
"""
from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import quote_plus, urlparse, urlunparse, parse_qs

from .base import ParsedOffer, RetailerAdapter


class AmazonIN(RetailerAdapter):
    key, name = "amazon", "Amazon India"
    domains = ("amazon.in",)
    short_domains = ("amzn.in", "amzn.to", "a.co")
    id_patterns = (r"/(?:dp|gp/product|d)/([A-Z0-9]{10})",)
    title_selectors = ("#productTitle", "span#title")
    price_selectors = ("#corePrice_feature_div .a-price .a-offscreen",
                       ".reinventPricePriceToPayMargin .a-price .a-offscreen",
                       "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
                       "#priceblock_ourprice", "#priceblock_dealprice")
    mrp_selectors = ("#corePrice_feature_div .a-text-price .a-offscreen", "#listPrice")

    def canonical_url(self, url):
        asin = self.external_id(url)
        return f"https://www.amazon.in/dp/{asin}" if asin else super().canonical_url(url)

    def search_url(self, q):
        return f"https://www.amazon.in/s?k={quote_plus(q)}"

    def postprocess(self, soup, out: ParsedOffer):
        if soup.select_one("#outOfStock"):
            out.availability = "out_of_stock"
        elif soup.select_one("#availability .a-color-success, #add-to-cart-button"):
            out.availability = "in_stock"
        img = soup.select_one("#landingImage")
        if img and not out.image_url:
            out.image_url = img.get("src")


class Flipkart(RetailerAdapter):
    key, name = "flipkart", "Flipkart"
    domains = ("flipkart.com",)
    short_domains = ("dl.flipkart.com", "fkrt.it", "fkrt.cc")
    id_patterns = (r"[?&]pid=([A-Z0-9]{10,20})", r"/p/(itm[a-z0-9]+)")
    title_selectors = ("span.VU-ZEz", "span.B_NuCI", "h1")
    price_selectors = ("div.Nx9bqj.CxhGGd", "div._30jeq3._16Jk6d", "div.Nx9bqj")
    mrp_selectors = ("div.yRaY8j", "div._3I9_wc")

    def canonical_url(self, url):
        p = urlparse(url)
        pid = (parse_qs(p.query).get("pid") or [None])[0]
        path = p.path.rstrip("/")
        return urlunparse(("https", "www.flipkart.com", path, "", f"pid={pid}" if pid else "", "")) if pid \
            else urlunparse(("https", "www.flipkart.com", path, "", "", ""))

    def search_url(self, q):
        return f"https://www.flipkart.com/search?q={quote_plus(q)}"

    def postprocess(self, soup, out):
        txt = soup.get_text(" ", strip=True).lower()
        if out.availability == "unknown":
            if "sold out" in txt or "currently unavailable" in txt:
                out.availability = "out_of_stock"


class JsonLdRetailer(RetailerAdapter):
    """Retailers where schema.org Product JSON-LD is expected. No bespoke selectors are claimed."""
    price_selectors = ()


class Croma(JsonLdRetailer):
    key, name, domains = "croma", "Croma", ("croma.com",)
    id_patterns = (r"/p/(\d+)",)
    def search_url(self, q): return f"https://www.croma.com/searchB?q={quote_plus(q)}"


class RelianceDigital(JsonLdRetailer):
    key, name, domains = "reliancedigital", "Reliance Digital", ("reliancedigital.in",)
    id_patterns = (r"/(\d{6,})(?:/|$)",)
    def search_url(self, q): return f"https://www.reliancedigital.in/search?q={quote_plus(q)}"


class VijaySales(JsonLdRetailer):
    key, name, domains = "vijaysales", "Vijay Sales", ("vijaysales.com",)
    id_patterns = (r"/(\d{4,})/?$",)
    def search_url(self, q): return f"https://www.vijaysales.com/search/{quote_plus(q)}"


class TataCliq(JsonLdRetailer):
    key, name, domains = "tatacliq", "Tata CLiQ", ("tatacliq.com",)
    id_patterns = (r"/p-(mp\d+)",)
    def search_url(self, q): return f"https://www.tatacliq.com/search/?searchCategory=all&text={quote_plus(q)}"
