from __future__ import annotations
from urllib.parse import urlparse
from .base import ExtractionError, ParsedOffer, RetailerAdapter
from .sites import AmazonIN, Croma, Flipkart, RelianceDigital, TataCliq, VijaySales

# Myntra and Ajio are intentionally not registered: they are apparel marketplaces whose catalogue
# data we have no verified access path for. Add an adapter here when one exists.
ADAPTERS: list[RetailerAdapter] = [AmazonIN(), Flipkart(), Croma(), RelianceDigital(), VijaySales(), TataCliq()]

def adapter_for_host(host: str) -> RetailerAdapter | None:
    return next((a for a in ADAPTERS if a.matches(host)), None)

def adapter_for_url(url: str) -> RetailerAdapter | None:
    return adapter_for_host(urlparse(url).hostname or "")

def adapter_by_key(key: str) -> RetailerAdapter | None:
    return next((a for a in ADAPTERS if a.key == key), None)

def allowed_domains() -> set[str]:
    return {d for a in ADAPTERS for d in a.domains}

def short_domains() -> set[str]:
    return {d for a in ADAPTERS for d in a.short_domains}
