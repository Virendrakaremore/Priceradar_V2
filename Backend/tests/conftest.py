import json
import pytest

from priceradar.app import create_app
from priceradar.config import Settings
from priceradar.fetcher import FetchResult, Fetcher
from priceradar.retailers.base import ExtractionError


def page(title, price, brand="Samsung", model="SM-S921B", availability="InStock", gtin=None, mrp=None):
    ld = {"@context": "https://schema.org", "@type": "Product", "name": title, "brand": {"@type": "Brand", "name": brand},
          "model": model, "image": "https://img.example/x.jpg",
          "offers": {"@type": "Offer", "price": str(price) if price is not None else None, "priceCurrency": "INR",
                     "availability": f"https://schema.org/{availability}"}}
    if price is None: ld["offers"].pop("price")
    if gtin: ld["gtin13"] = gtin
    return f"<html><head><title>{title}</title><script type='application/ld+json'>{json.dumps(ld)}</script></head><body><h1>{title}</h1></body></html>"


class FakeFetcher(Fetcher):
    def __init__(self): self.pages, self.calls = {}, []
    def set(self, url, html=None, error=None): self.pages[url] = html if html is not None else error
    def fetch(self, url, *, render=False):
        self.calls.append((url, render))
        v = self.pages.get(url)
        if v is None: raise ExtractionError("not_found", "no fixture", retryable=False)
        if isinstance(v, ExtractionError): raise v
        return FetchResult(v, url, 200, "fake")


AMZ = "https://www.amazon.in/dp/B0CMDRCZ1Y"
FK = "https://www.flipkart.com/samsung-galaxy-s24/p/itm123abc?pid=MOBGX2F3XXXXXXXX"
CROMA = "https://www.croma.com/samsung-galaxy-s24/p/300001"
TITLE = "Samsung Galaxy S24 5G (Onyx Black, 8GB RAM, 256GB Storage) SM-S921B"


@pytest.fixture
def fetcher(): return FakeFetcher()


@pytest.fixture
def settings(tmp_path):
    return Settings(database_url=f"sqlite:///{tmp_path/'t.db'}", smtp_host="smtp.test", smtp_username="u", smtp_password="p",
                    email_from="alerts@test.dev", default_user_email="me@example.com", run_jobs_inline=True,
                    retailer_min_interval_s=15, monitor_interval_s=15, cors_origins=["http://localhost:3000"])


@pytest.fixture
def sent():
    return []


@pytest.fixture
def app(settings, fetcher, sent):
    return create_app(settings, fetcher=fetcher, providers=[], create_tables=True, check_dns=False,
                      smtp_sender=lambda s, m: sent.append(m))


@pytest.fixture
def client(app): return app.test_client()


@pytest.fixture
def db(app):
    s = app.config["SESSION_FACTORY"]()
    yield s
    s.close()
