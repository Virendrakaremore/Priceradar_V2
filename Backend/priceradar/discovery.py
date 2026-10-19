"""Provider-based discovery of the same product at other retailers.

Implemented providers:
  * RetailerSearchProvider – loads each retailer's public search page (subject to robots.txt and
    to the retailer serving us) and collects product links. Results are only CANDIDATES; every
    candidate is fetched, parsed and must pass the matching gate before it is shown as verified.
  * Manual offers – the user pastes the other retailer's URL (see API POST /products/<id>/offers).

NOT implemented: aggregator/affiliate-feed providers (Google Shopping, Admitad, Cuelinks, ...).
Implement ``DiscoveryProvider`` and register it in ``default_providers`` once you have credentials.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .fetcher import Fetcher
from .retailers import ADAPTERS
from .retailers.base import ExtractionError

log = logging.getLogger("priceradar.discovery")


@dataclass
class Candidate:
    retailer_key: str
    url: str
    provider: str


class DiscoveryProvider:
    name = "base"
    def discover(self, query: str, exclude_retailer: str) -> tuple[list[Candidate], list[str]]:  # pragma: no cover
        raise NotImplementedError


class RetailerSearchProvider(DiscoveryProvider):
    name = "retailer_search"

    def __init__(self, fetcher: Fetcher, per_retailer: int = 4):
        self.fetcher, self.per_retailer = fetcher, per_retailer

    def discover(self, query, exclude_retailer):
        found: list[Candidate] = []
        warnings: list[str] = []
        for ad in ADAPTERS:
            if ad.key == exclude_retailer:
                continue
            surl = ad.search_url(query)
            if not surl:
                continue
            try:
                res = self.fetcher.fetch(surl, render=True)
            except ExtractionError as e:
                warnings.append(f"{ad.name}: search unavailable ({e.category}).")
                continue
            soup = BeautifulSoup(res.html, "lxml")
            seen: set[str] = set()
            for a in soup.find_all("a", href=True):
                href = urljoin(res.final_url, a["href"])
                if not ad.matches(urlparse(href).hostname or ""):
                    continue
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
                warnings.append(f"{ad.name}: no product links found in search results.")
        return found, warnings


def build_query(brand: str | None, model_codes: set[str], title: str) -> str:
    if model_codes:
        code = sorted(model_codes, key=len, reverse=True)[0].upper()
        return f"{brand or ''} {code}".strip()
    words = re.sub(r"[^A-Za-z0-9 ]+", " ", title).split()
    return " ".join(words[:7])


def default_providers(fetcher: Fetcher) -> list[DiscoveryProvider]:
    return [RetailerSearchProvider(fetcher)]
