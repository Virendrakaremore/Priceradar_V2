from datetime import datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from priceradar import analytics, matching
from priceradar.analytics import Point
from priceradar.pricing import parse_price
from priceradar.retailers import adapter_for_url, ADAPTERS
from priceradar.retailers.base import ExtractionError
from priceradar.security import UrlRejected, extract_url, resolve_redirects, validate_url
from priceradar.retailers import allowed_domains, short_domains
from tests.conftest import page, TITLE

NOW = datetime(2026, 10, 14, tzinfo=timezone.utc)


# ---- price parsing
@pytest.mark.parametrize("raw,exp", [("₹1,29,999.00", D("129999.00")), ("Rs. 1,299", D("1299.00")), (49999, D("49999.00")), ("12.5", D("12.50"))])
def test_parse_price_ok(raw, exp): assert parse_price(raw) == exp

@pytest.mark.parametrize("raw", [None, "", "free", "0", 0, -5, "₹0"])
def test_parse_price_never_zero_or_garbage(raw): assert parse_price(raw) is None


# ---- URL handling / SSRF
def test_extract_url_from_share_text():
    assert extract_url("Check this out! https://dl.flipkart.com/s/abc123. Cheers") == "https://dl.flipkart.com/s/abc123"

def test_extract_url_rejects_empty_and_no_link():
    with pytest.raises(UrlRejected): extract_url("   ")
    with pytest.raises(UrlRejected): extract_url("no link here")

@pytest.mark.parametrize("url", ["http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data", "https://evil.com/amazon.in",
                                 "https://amazon.in.evil.com/dp/B0CMDRCZ1Y", "ftp://amazon.in/x", "https://user:pw@amazon.in/dp/X",
                                 "https://www.amazon.in:8080/dp/B0CMDRCZ1Y", "http://localhost:5000/"])
def test_ssrf_and_unsupported_rejected(url):
    with pytest.raises(UrlRejected): validate_url(url, allowed_domains() | short_domains(), check_dns=False)

def test_private_dns_resolution_blocked(monkeypatch):
    import socket
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 0))])
    with pytest.raises(UrlRejected) as e: validate_url("https://www.amazon.in/dp/B0CMDRCZ1Y", allowed_domains())
    assert e.value.code == "ssrf_blocked"

class _Resp:
    def __init__(self, code, loc=None): self.status_code, self.headers = code, ({"Location": loc} if loc else {})
    def close(self): pass

class _Sess:
    def __init__(self, hops): self.hops, self.n = hops, 0
    def get(self, url, **k): r = self.hops[self.n]; self.n += 1; return r

def test_short_link_expansion_follows_validated_hops():
    s = _Sess([_Resp(302, "https://www.amazon.in/dp/B0CMDRCZ1Y?ref=x")])
    assert resolve_redirects("https://amzn.in/d/abc", allowed_domains(), short_domains(), s, check_dns=False) == "https://www.amazon.in/dp/B0CMDRCZ1Y?ref=x"

def test_short_link_redirect_to_internal_host_blocked():
    s = _Sess([_Resp(302, "http://169.254.169.254/latest")])
    with pytest.raises(UrlRejected): resolve_redirects("https://amzn.in/d/abc", allowed_domains(), short_domains(), s, check_dns=False)

def test_full_url_not_fetched():
    class Boom:
        def get(self, *a, **k): raise AssertionError("no network for full URLs")
    assert resolve_redirects("https://www.flipkart.com/x/p/itm1?pid=ABCDEFGHIJKL", allowed_domains(), short_domains(), Boom(), check_dns=False)


# ---- adapters
def test_adapter_selection_and_canonicalisation():
    assert adapter_for_url("https://www.amazon.in/Some-Title/dp/B0CMDRCZ1Y/ref=sr_1_1?x=1").canonical_url("https://www.amazon.in/Some-Title/dp/B0CMDRCZ1Y/ref=sr_1_1?x=1") == "https://www.amazon.in/dp/B0CMDRCZ1Y"
    assert adapter_for_url("https://dl.flipkart.com/s/x").key == "flipkart"
    assert adapter_for_url("https://www.croma.com/p/1").key == "croma"
    assert adapter_for_url("https://example.com/") is None
    assert all(not a.live_verified for a in ADAPTERS)   # honesty guard: nothing claims live verification yet

def test_jsonld_parse_extracts_fields():
    ad = adapter_for_url("https://www.croma.com/p/1")
    p = ad.parse(page(TITLE, "74999", gtin="8806095000000"), "https://www.croma.com/x/p/300001")
    assert p.price == D("74999.00") and p.availability == "in_stock" and p.gtin == "8806095000000" and p.confidence >= D("0.9")

def test_missing_price_is_none_not_zero():
    ad = adapter_for_url("https://www.croma.com/p/1")
    assert ad.parse(page(TITLE, None, availability="OutOfStock"), "https://www.croma.com/p/1").price is None

def test_captcha_page_reported_as_blocked():
    ad = adapter_for_url("https://www.amazon.in/dp/B0CMDRCZ1Y")
    with pytest.raises(ExtractionError) as e: ad.parse("<html><title>Robot Check</title>Enter the characters you see</html>", "https://www.amazon.in/dp/B0CMDRCZ1Y")
    assert e.value.category == "blocked"

def test_selector_fallback_amazon():
    ad = adapter_for_url("https://www.amazon.in/dp/B0CMDRCZ1Y")
    html = ("<html><body><span id='productTitle'>Thing 128GB</span><div id='corePrice_feature_div'><span class='a-price'>"
            "<span class='a-offscreen'>₹12,499.00</span></span><span class='a-price a-text-price'><span class='a-offscreen'>₹15,999</span></span></div></body></html>")
    p = ad.parse(html, "https://www.amazon.in/dp/B0CMDRCZ1Y")
    assert p.price == D("12499.00") and p.source == "selector" and p.confidence < D("0.9")


# ---- matching
def A(t, **k): return matching.extract_attrs(t, **k)
S = TITLE

def test_same_model_verified():
    c = "Samsung Galaxy S24 5G Smartphone Onyx Black 8GB 256GB SM-S921B"
    assert matching.compare(A(S), S, A(c), c).status == "verified"

def test_gtin_equal_verified_conflict_rejected():
    assert matching.compare(A("X", gtin="0123456789012"), "X", A("Y", gtin="123456789012"), "Y").status == "verified"
    assert matching.compare(A(S, gtin="1111111111111"), S, A(S, gtin="2222222222222"), S).status == "rejected"

def test_different_storage_rejected():
    c = "Samsung Galaxy S24 5G (Onyx Black, 8GB RAM, 128GB Storage) SM-S921B"
    r = matching.compare(A(S), S, A(c), c)
    assert r.status == "rejected" and any("storage" in x.lower() for x in r.reasons)

def test_different_colour_only_possible():
    c = "Samsung Galaxy S24 5G (Cobalt Violet, 8GB RAM, 256GB Storage) SM-S921B"
    assert matching.compare(A(S), S, A(c), c).status == "possible"

def test_different_model_number_rejected():
    c = "Samsung Galaxy S24 Ultra 5G (Onyx Black, 8GB RAM, 256GB Storage) SM-S928B"
    assert matching.compare(A(S), S, A(c), c).status == "rejected"

def test_similar_words_without_identifier_is_only_possible():
    a, b = "Acme Wireless Headphones Black Over Ear", "Acme Wireless Headphones Black Over-Ear Bluetooth"
    assert matching.compare(A(a), a, A(b), b).status == "possible"

def test_bundle_mismatch_rejected():
    c = S + " Combo with Free Earbuds"
    assert matching.compare(A(S), S, A(c), c).status == "rejected"


# ---- analytics
def P(days_ago, price): return Point(NOW - timedelta(days=days_ago), D(str(price)))

def test_time_weighted_stats_not_biased_by_change_points():
    pts = [P(10, 10000), P(9.9, 8000), P(9.8, 8100)]   # briefly cheap, then held at 8100 for 9.8d
    st = analytics.compute_stats(pts, NOW)
    assert st["min"] == D("8000.00") and st["max"] == D("10000.00") and st["current"] == D("8100.00")
    assert D("8100") <= st["average"] < D("8120") and st["median"] == D("8100.00")  # unweighted mean would be 8700

def test_changes_over_windows():
    pts = [P(40, 12000), P(20, 11000), P(5, 10000), P(1, 9500)]
    st = analytics.compute_stats(pts, NOW)
    # price in force 7 days ago was 11000 (set at day 20); 30 days ago it was 12000
    assert st["change_7d"] == D("-1500.00") and st["change_30d"] == D("-2500.00")
    assert st["change_7d_pct"] == D("-13.64")

def test_insufficient_history_flags_preliminary():
    st = analytics.compute_stats([P(0.1, 5000)], NOW)
    v = analytics.classify(st)
    assert st["sufficient"] is False and v["preliminary"] and "Preliminary" in (v["recommendation"] or "") or "not enough" in v["recommendation"].lower()

def test_classification_labels():
    base = [P(30, 12000), P(25, 11000), P(20, 10000), P(15, 9500), P(10, 9000)]
    low = analytics.classify(analytics.compute_stats(base + [P(1, 8000)], NOW))
    assert low["label"] == "historically_low" and not low["preliminary"]
    high = analytics.classify(analytics.compute_stats(base + [P(1, 12000)], NOW))
    assert high["label"] == "historically_high" and "waiting" in high["recommendation"]

def test_target_suggestions_and_times_reached():
    pts = [P(30, 12000), P(25, 8000), P(20, 12000), P(15, 8000), P(10, 12000), P(2, 10000)]
    t = analytics.target_suggestions(pts)
    assert t["min"] == D("8000.00") and t["max"] == D("12000.00")
    low = next(s for s in t["suggestions"] if s["key"] == "historical_low")
    assert low["price"] == D("8000.00") and low["saving"] == D("2000.00") and low["pct_drop_needed"] == D("20.00") and low["times_reached"] == 2
    assert t["slider"]["min"] < t["min"]          # targets below the recorded low are allowed
    assert analytics.target_suggestions([])["available"] is False

def test_bucketing_preserves_extremes():
    pts = [Point(NOW - timedelta(minutes=i), D(str(100 + (i % 7)))) for i in range(300)]
    rows = analytics.bucketize(pts, timedelta(hours=1))
    assert min(r["min"] for r in rows) == D("100") and max(r["max"] for r in rows) == D("106")
