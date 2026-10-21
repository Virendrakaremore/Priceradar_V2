"""Concrete retailer adapters for Indian e-commerce.

Selector lists are best-effort fallbacks behind JSON-LD / meta-tag parsing.
JSON-LD is tried first (most reliable), then OpenGraph meta tags, then CSS selectors.

``live_verified`` is True only once selectors were confirmed against live pages.
All adapters parse the product detail page; discovery (search) uses ``search_url()``.

Added in this revision:
  * JioMart adapter (jiomartexpress.com / jiomart.com)
  * Improved Flipkart selectors (2024/2025 class names)
  * Amazon fallback: parse data-a-color-price span, swatches
  * Croma/Reliance/VijaySales: improved JSON-LD extraction path
  * TataCliq: improved id_patterns to handle /buy/ URLs
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
    live_verified = True
    id_patterns = (r"/(?:dp|gp/product|d)/([A-Z0-9]{10})",)
    title_selectors = ("#productTitle", "span#title", "h1.a-size-large")
    price_selectors = (
        "#corePrice_feature_div .a-price .a-offscreen",
        ".reinventPricePriceToPayMargin .a-price .a-offscreen",
        "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
        "#apex_desktop .a-price .a-offscreen",
        "[data-a-color='price'] .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#price_inside_buybox",
    )
    mrp_selectors = (
        "#corePrice_feature_div .a-text-price .a-offscreen",
        "#listPrice",
        ".basisPrice .a-offscreen",
    )

    def canonical_url(self, url: str) -> str:
        asin = self.external_id(url)
        return f"https://www.amazon.in/dp/{asin}" if asin else super().canonical_url(url)

    def search_url(self, q: str) -> str:
        return f"https://www.amazon.in/s?k={quote_plus(q)}"

    def postprocess(self, soup, out: ParsedOffer):
        if soup.select_one("#outOfStock"):
            out.availability = "out_of_stock"
        elif soup.select_one("#availability .a-color-success, #add-to-cart-button"):
            out.availability = "in_stock"
        # Image
        img = soup.select_one("#landingImage, #imgBlkFront")
        if img and not out.image_url:
            out.image_url = img.get("src") or img.get("data-old-hires")


class Flipkart(RetailerAdapter):
    key, name = "flipkart", "Flipkart"
    domains = ("flipkart.com",)
    short_domains = ("dl.flipkart.com", "fkrt.it", "fkrt.cc")
    live_verified = True
    id_patterns = (r"[?&]pid=([A-Z0-9]{10,20})", r"/p/(itm[a-z0-9]+)")
    title_selectors = (
        "span.VU-ZEz",        # 2024 class
        "span.B_NuCI",        # older class
        "h1.yhB1nd",
        "h1",
    )
    price_selectors = (
        "div.Nx9bqj.CxhGGd",   # 2024 current price
        "div._30jeq3._16Jk6d", # older
        "div.Nx9bqj",
        "div._16Jk6d",
        "div.dyC4hf",
    )
    mrp_selectors = (
        "div.yRaY8j",
        "div._3I9_wc",
        "div.ZBMO9O",
    )

    def canonical_url(self, url: str) -> str:
        p = urlparse(url)
        pid = (parse_qs(p.query).get("pid") or [None])[0]
        path = p.path.rstrip("/")
        if pid:
            return urlunparse(("https", "www.flipkart.com", path, "", f"pid={pid}", ""))
        return urlunparse(("https", "www.flipkart.com", path, "", "", ""))

    def search_url(self, q: str) -> str:
        return f"https://www.flipkart.com/search?q={quote_plus(q)}"

    def postprocess(self, soup, out: ParsedOffer):
        txt = soup.get_text(" ", strip=True).lower()
        if out.availability == "unknown":
            if "sold out" in txt or "currently unavailable" in txt:
                out.availability = "out_of_stock"
            elif "add to cart" in txt or "buy now" in txt:
                out.availability = "in_stock"
        # Image
        img = soup.select_one("img._396cs4, img.q6DClP, div._3SrU-M img")
        if img and not out.image_url:
            out.image_url = img.get("src")


class JsonLdRetailer(RetailerAdapter):
    """Base for retailers where schema.org Product JSON-LD is the primary data source."""
    price_selectors = ()


class Croma(JsonLdRetailer):
    key, name = "croma", "Croma"
    domains = ("croma.com",)
    id_patterns = (r"/p/(\d+)", r"-(\d{7,})(?:\?|$|/)")
    live_verified = False

    def search_url(self, q: str) -> str:
        return f"https://www.croma.com/searchB?q={quote_plus(q)}&langCode=en"

    def postprocess(self, soup, out: ParsedOffer):
        # Croma often puts price in a span with class cp-price
        if out.price is None:
            for sel in ("span.cp-price", "li.price", "span[class*='price']"):
                el = soup.select_one(sel)
                if el:
                    from ..pricing import parse_price
                    p = parse_price(el.get_text(" ", strip=True))
                    if p:
                        out.price = p
                        out.source = "selector"
                        from decimal import Decimal
                        out.confidence = Decimal("0.7")
                        break
        if out.availability == "unknown":
            txt = soup.get_text(" ", strip=True).lower()
            if "add to cart" in txt or "buy now" in txt:
                out.availability = "in_stock"
            elif "out of stock" in txt or "notify me" in txt:
                out.availability = "out_of_stock"


class RelianceDigital(JsonLdRetailer):
    key, name = "reliancedigital", "Reliance Digital"
    domains = ("reliancedigital.in",)
    id_patterns = (r"/(\d{6,})(?:/|$)",)
    live_verified = False

    def search_url(self, q: str) -> str:
        return f"https://www.reliancedigital.in/search?q={quote_plus(q)}"

    def postprocess(self, soup, out: ParsedOffer):
        if out.price is None:
            for sel in ("span.price", "p.TextWeb__Text-sc", "span[class*='price']"):
                el = soup.select_one(sel)
                if el:
                    from ..pricing import parse_price
                    p = parse_price(el.get_text(" ", strip=True))
                    if p:
                        out.price = p
                        out.source = "selector"
                        from decimal import Decimal
                        out.confidence = Decimal("0.65")
                        break
        if out.availability == "unknown":
            txt = soup.get_text(" ", strip=True).lower()
            if "add to cart" in txt:
                out.availability = "in_stock"
            elif "out of stock" in txt or "notify me" in txt:
                out.availability = "out_of_stock"


class VijaySales(JsonLdRetailer):
    key, name = "vijaysales", "Vijay Sales"
    domains = ("vijaysales.com",)
    id_patterns = (r"/(\d{4,})/?$", r"-(\d{5,})(?:\?|$)")
    live_verified = False

    def search_url(self, q: str) -> str:
        return f"https://www.vijaysales.com/search/{quote_plus(q)}"


class TataCliq(JsonLdRetailer):
    key, name = "tatacliq", "Tata CLiQ"
    domains = ("tatacliq.com",)
    id_patterns = (r"/p-(mp\d+)", r"/buy/[^/]+-(\d+)")
    live_verified = False

    def search_url(self, q: str) -> str:
        return f"https://www.tatacliq.com/search/?searchCategory=all&text={quote_plus(q)}"


class JioMart(JsonLdRetailer):
    """
    JioMart (jiomartexpress.com / jiomart.com) adapter.

    JioMart uses schema.org JSON-LD on product pages, so the base JSON-LD extraction
    in RetailerAdapter._from_jsonld() handles most cases. We add CSS selector fallbacks
    and a postprocess hook for availability / image.
    """
    key, name = "jiomart", "JioMart"
    domains = ("jiomart.com", "jiomartexpress.com")
    short_domains = ()
    live_verified = False
    id_patterns = (
        r"/p/(\d{6,})",           # standard product page
        r"-(\d{6,})(?:\?|$|/)",   # slug-based URLs
    )
    title_selectors = (
        "h1.jm-heading-xxs",
        "h1.product-title",
        "h1",
    )
    price_selectors = (
        "span.jm-heading-xxs.jm-mb-xxs",
        "span.price",
        "p.price",
        "[class*='final-price']",
        "[class*='selling-price']",
    )
    mrp_selectors = (
        "span.line-through",
        "[class*='mrp']",
        "[class*='original-price']",
    )

    def search_url(self, q: str) -> str:
        return f"https://www.jiomart.com/search#q={quote_plus(q)}&t=product"

    def canonical_url(self, url: str) -> str:
        p = urlparse(url)
        # Normalise to jiomart.com regardless of the subdomain used
        host = "www.jiomart.com"
        return urlunparse(("https", host, p.path.rstrip("/") or "/", "", "", ""))

    def postprocess(self, soup, out: ParsedOffer):
        if out.availability == "unknown":
            txt = soup.get_text(" ", strip=True).lower()
            if "add to cart" in txt or "buy now" in txt:
                out.availability = "in_stock"
            elif "out of stock" in txt or "notify me" in txt:
                out.availability = "out_of_stock"
        img = soup.select_one("img.product-image, img[class*='product-img'], div.slick-slide img")
        if img and not out.image_url:
            out.image_url = img.get("src") or img.get("data-src")
