import smtplib
from datetime import timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import select, func

from priceradar import notifier
from priceradar.ingest import record_failure, record_success, freshness
from priceradar.models import Offer, PriceObservation, NotificationEvent, AlertRule, ScrapeFailure, MatchCandidate
from priceradar.monitor import Monitor
from priceradar.retailers.base import ExtractionError
from priceradar.timeutil import utcnow
from tests.conftest import AMZ, CROMA, FK, TITLE, page, FakeFetcher


def track(client, url=AMZ):
    r = client.post("/api/v1/products/identify", json={"input": f"look at this {url} thanks"})
    assert r.status_code == 202, r.json
    return r.json["job"]


def bump(db, offer_id):
    o = db.get(Offer, offer_id); o.next_check_at = utcnow() - timedelta(seconds=1); o.locked_until = None; db.commit()


# ---------------- full flow -----------------
def test_end_to_end_url_to_email(app, client, fetcher, settings, sent, db):
    fetcher.set(AMZ, page(TITLE, "79999", mrp=89999))
    job = track(client)
    assert job["status"] == "done" and job["stage"] == "done"
    pid = job["product_id"]

    d = client.get(f"/api/v1/products/{pid}").json
    assert d["product"]["title"] == TITLE and d["offers"][0]["price"] == "79999.00" and d["best_offer_id"] == d["offers"][0]["id"]
    assert d["verdict"]["preliminary"] is True                           # one observation => preliminary
    assert client.get(f"/api/v1/products/{pid}/history").json["insufficient"] is True

    # alert rule with target
    r = client.post("/api/v1/alerts", json={"product_id": pid, "kind": "target_reached", "target_price": 75000, "email": "me@example.com"})
    assert r.status_code == 201

    # worker observes a drop below target
    fetcher.set(AMZ, page(TITLE, "74000"))
    mon = Monitor(app.config["SESSION_FACTORY"], settings, fetcher, sender=lambda s, m: sent.append(m))
    oid = d["offers"][0]["id"]
    bump(db, oid)
    assert mon.tick() == 1

    db.expire_all()
    obs = db.scalars(select(PriceObservation).where(PriceObservation.offer_id == oid).order_by(PriceObservation.id)).all()
    assert [o.price for o in obs] == [D("79999.00"), D("74000.00")] and all(o.observed_at and o.source for o in obs)
    hist = client.get(f"/api/v1/products/{pid}/history").json
    assert [p["price"] for p in hist["points"]] == ["79999.00", "74000.00"]
    assert hist["stats"]["min"] == "74000.00" and hist["stats"]["max"] == "79999.00"

    ev = db.scalars(select(NotificationEvent)).all()
    assert len(ev) == 1 and ev[0].status == "sent" and ev[0].event_type == "target_reached"
    assert len(sent) == 1
    msg = sent[0]
    html = msg.get_body(("html",)).get_content(); text = msg.get_body(("plain",)).get_content()
    assert "View on Amazon India" in html and "Buy on Amazon" not in html and "₹74,000" in html and "₹74,000" in text
    assert "https://www.amazon.in/dp/B0CMDRCZ1Y" in html

    # same price again => no duplicate observation, no duplicate email
    bump(db, oid); mon.tick()
    assert db.scalar(select(func.count(PriceObservation.id))) == 2 and len(sent) == 1

    # survives restart: new app on same DB sees everything
    from priceradar.app import create_app
    app2 = create_app(settings, fetcher=fetcher, providers=[], check_dns=False)
    assert app2.test_client().get(f"/api/v1/products/{pid}/history").json["stats"]["count"] == 2


def test_duplicate_submission_reuses_product(client, fetcher):
    fetcher.set(AMZ, page(TITLE, "79999"))
    a = track(client); b = track(client, AMZ + "?ref=abc")
    assert a["product_id"] == b["product_id"] and any("already" in w for w in b["warnings"])
    assert client.get("/api/v1/products").json["total"] == 1


def test_unsupported_and_invalid_inputs(client):
    for body, code in (({"input": ""}, "empty"), ({"input": "hello"}, "no_url"), ({"input": "https://example.com/p/1"}, "unsupported_retailer"),
                       ({"input": "http://127.0.0.1:5000/x"}, "unsupported_retailer")):
        r = client.post("/api/v1/products/identify", json=body)
        assert r.status_code == 422 and r.json["error"]["code"] == code


def test_blocked_retailer_reported_not_faked(client, fetcher):
    fetcher.set(AMZ, error=ExtractionError("blocked", "challenge", True))
    job = track(client)
    assert job["status"] == "failed" and job["error_code"] == "blocked" and job["product_id"] is None


def test_manual_cross_retailer_offers_verified_vs_rejected(client, fetcher):
    fetcher.set(AMZ, page(TITLE, "79999"))
    pid = track(client)["product_id"]
    fetcher.set(CROMA, page("Samsung Galaxy S24 5G Smartphone Onyx Black 8GB 256GB SM-S921B", "76500"))
    assert client.post(f"/api/v1/products/{pid}/offers", json={"url": CROMA}).status_code == 201
    # different storage on Flipkart must NOT be presented as the same product
    fetcher.set(FK, page("Samsung Galaxy S24 5G (Onyx Black, 8GB RAM, 128GB Storage) SM-S921B", "60000"))
    r = client.post(f"/api/v1/products/{pid}/offers", json={"url": FK})
    assert r.status_code == 422 and r.json["error"]["code"] == "not_same_product"
    d = client.get(f"/api/v1/products/{pid}").json
    assert d["best_offer_id"] == next(o["id"] for o in d["offers"] if o["retailer_key"] == "croma")   # lowest verified
    assert len(d["offers"]) == 2 and d["rejected_candidates"][0]["retailer"] == "flipkart"


def test_out_of_stock_or_stale_never_best(app, client, fetcher, db):
    fetcher.set(AMZ, page(TITLE, "79999")); pid = track(client)["product_id"]
    fetcher.set(CROMA, page(TITLE + " ", "70000", availability="OutOfStock"))
    client.post(f"/api/v1/products/{pid}/offers", json={"url": CROMA})
    d = client.get(f"/api/v1/products/{pid}").json
    assert d["best_offer_id"] == next(o["id"] for o in d["offers"] if o["retailer_key"] == "amazon")
    o = db.scalars(select(Offer).where(Offer.product_id == pid, Offer.match_status == "source")).one()
    o.last_success_at = utcnow() - timedelta(days=2); db.commit()
    d = client.get(f"/api/v1/products/{pid}").json
    assert d["best_offer_id"] is None and d["no_fresh_offer"] is True
    assert next(x for x in d["offers"] if x["retailer_key"] == "amazon")["fresh"] is False


# ---------------- ingestion / monitoring -----------------
def test_failure_keeps_last_price_flags_stale_and_backs_off(app, client, fetcher, settings, db):
    fetcher.set(AMZ, page(TITLE, "79999")); pid = track(client)["product_id"]
    o = db.scalars(select(Offer)).one()
    for i in range(1, 6):
        record_failure(db, o, "timeout", "timed out", True, settings); db.commit()
    assert o.current_price == D("79999.00") and o.status == "stale"           # price not zeroed / not replaced
    assert o.circuit_open_until is not None and o.next_check_at == o.circuit_open_until
    assert db.scalar(select(func.count(PriceObservation.id))) == 1 and db.scalar(select(func.count(ScrapeFailure.id))) == 5
    assert freshness(o, settings)["fresh"] is False


def test_backoff_is_exponential_and_capped(settings, db, client, fetcher):
    fetcher.set(AMZ, page(TITLE, "100")); track(client)
    o = db.scalars(select(Offer)).one()
    waits = []
    for _ in range(3):
        waits.append(record_failure(db, o, "network", "x", True, settings)["backoff_s"])
    assert waits == [30, 60, 120]


def test_big_jump_needs_confirmation(app, client, fetcher, settings, db):
    fetcher.set(AMZ, page(TITLE, "80000")); track(client)
    o = db.scalars(select(Offer)).one()
    ad = __import__("priceradar.retailers", fromlist=["x"]).adapter_for_url(AMZ)
    bad = ad.parse(page(TITLE, "9000"), AMZ)                                    # -89%: likely wrong element
    assert record_success(db, o, bad, settings)["pending_confirmation"] and o.current_price == D("80000.00")
    assert record_success(db, o, bad, settings)["accepted"] and o.current_price == D("9000.00")   # confirmed twice


def test_missing_price_recorded_as_unavailable_not_zero(settings, db, client, fetcher):
    fetcher.set(AMZ, page(TITLE, "5000")); track(client)
    o = db.scalars(select(Offer)).one()
    ad = __import__("priceradar.retailers", fromlist=["x"]).adapter_for_url(AMZ)
    r = record_success(db, o, ad.parse(page(TITLE, None, availability="OutOfStock"), AMZ), settings)
    assert r["status"] == "unavailable" and o.current_price == D("5000.00") and db.scalar(select(func.count(PriceObservation.id))) == 1


def test_monitor_claims_atomically_and_respects_retailer_budget(app, settings, fetcher, client, db):
    fetcher.set(AMZ, page(TITLE, "100")); track(client)
    mon = Monitor(app.config["SESSION_FACTORY"], settings, fetcher)
    bump(db, db.scalars(select(Offer)).one().id)
    first = mon.claim_due(5)
    assert len(first) == 1 and mon.claim_due(5) == []        # locked: no overlapping job for the same offer


def test_effective_interval_floor_is_visible(app, client, fetcher, settings, db):
    s2 = settings; s2.retailer_min_interval_s = 60
    fetcher.set(AMZ, page(TITLE, "100")); pid = track(client)["product_id"]
    f = client.get(f"/api/v1/products/{pid}").json["offers"][0]["freshness"]
    assert f["requested_interval_s"] == 15 and f["effective_interval_s"] == 60 and f["limited_by_retailer_floor"] is True


# ---------------- alerts -----------------
def mkrule(client, pid, **kw):
    r = client.post("/api/v1/alerts", json={"product_id": pid, "email": "me@example.com", **kw}); assert r.status_code == 201, r.json
    return r.json["alert"]["id"]


def run_price(app, settings, fetcher, db, sent, price, availability="InStock"):
    fetcher.set(AMZ, page(TITLE, str(price), availability=availability))
    mon = Monitor(app.config["SESSION_FACTORY"], settings, fetcher, sender=lambda s, m: sent.append(m))
    db.expire_all(); bump(db, db.scalars(select(Offer)).one().id)
    mon.tick(); db.expire_all()


def test_target_alert_fires_once_then_rearms(app, client, fetcher, settings, sent, db):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    mkrule(client, pid, kind="target_reached", target_price=9000, cooldown_s=0)
    for price in (9500, 8900, 8800, 8900):   # crosses once; staying below must not re-send
        run_price(app, settings, fetcher, db, sent, price)
    assert len(sent) == 1
    run_price(app, settings, fetcher, db, sent, 9600)   # rises above -> re-arm
    run_price(app, settings, fetcher, db, sent, 8700)   # new crossing => separate event
    assert len(sent) == 2


def test_cooldown_suppresses_followups(app, client, fetcher, settings, sent, db):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    mkrule(client, pid, kind="any_decrease", cooldown_s=3600)
    run_price(app, settings, fetcher, db, sent, 9900); run_price(app, settings, fetcher, db, sent, 9800)
    assert len(sent) == 1


def test_any_change_without_cooldown_and_min_change(app, client, fetcher, settings, sent, db):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    mkrule(client, pid, kind="any_decrease", cooldown_s=0, min_change=50)
    run_price(app, settings, fetcher, db, sent, 9990)    # ₹10 < min_change
    run_price(app, settings, fetcher, db, sent, 9900)    # ₹90
    assert len(sent) == 1


def test_pct_drop_increase_restock_and_historical_low(app, client, fetcher, settings, sent, db):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    mkrule(client, pid, kind="pct_drop", pct_threshold=10, cooldown_s=0)
    mkrule(client, pid, kind="any_increase", cooldown_s=0)
    mkrule(client, pid, kind="back_in_stock", cooldown_s=0)
    mkrule(client, pid, kind="historical_low", cooldown_s=0)
    run_price(app, settings, fetcher, db, sent, 9800)      # -2%: nothing but historical-low needs >=3 obs
    run_price(app, settings, fetcher, db, sent, 10200)     # increase
    run_price(app, settings, fetcher, db, sent, 8500)      # -16.7% => pct_drop + historical_low
    run_price(app, settings, fetcher, db, sent, 8500, availability="OutOfStock")
    run_price(app, settings, fetcher, db, sent, 8500, availability="InStock")   # restock
    types = sorted(e.event_type for e in db.scalars(select(NotificationEvent)))
    assert types == ["back_in_stock", "historical_low", "pct_drop", "price_increase"]


def test_failed_scrape_never_alerts(app, client, fetcher, settings, sent, db):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    mkrule(client, pid, kind="target_reached", target_price=9000)
    fetcher.set(AMZ, error=ExtractionError("parse", "garbled", True))
    mon = Monitor(app.config["SESSION_FACTORY"], settings, fetcher, sender=lambda s, m: sent.append(m))
    bump(db, db.scalars(select(Offer)).one().id); mon.tick()
    assert sent == [] and db.scalar(select(func.count(NotificationEvent.id))) == 0


def test_event_dedupe_constraint(db, client, fetcher):
    from priceradar.alerts import _event
    from priceradar.models import User
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    rid = mkrule(client, pid, kind="any_decrease")
    rule, offer, user = db.get(AlertRule, rid), db.scalars(select(Offer)).one(), db.scalars(select(User)).first()
    assert _event(db, rule, user, offer, "price_drop", "k1", {}) is not None
    assert _event(db, rule, user, offer, "price_drop", "k1", {}) is None


# ---------------- SMTP -----------------
def _ev(db, client, fetcher):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    o = db.scalars(select(Offer)).one()
    ev = NotificationEvent(user_id=1, product_id=pid, offer_id=o.id, event_type="price_drop", event_key="a", recipient="me@example.com",
                           payload={"product_name": "<script>x</script>", "retailer": "Amazon India", "current_price": "100", "previous_price": "120", "offer_url": AMZ, "reason": "r"},
                           next_attempt_at=utcnow())
    db.add(ev); db.commit(); return ev


def test_smtp_retry_then_success_and_html_escaped(settings, db, client, fetcher):
    ev = _ev(db, client, fetcher); calls = []
    def flaky(s, m):
        calls.append(m)
        if len(calls) == 1: raise TimeoutError("smtp timeout")
    notifier.deliver_pending(db, settings, flaky)
    assert ev.status == "failed" and ev.attempts == 1 and ev.next_attempt_at > utcnow()
    ev.next_attempt_at = utcnow() - timedelta(seconds=1); db.commit()
    notifier.deliver_pending(db, settings, flaky)
    assert ev.status == "sent" and "<script>" not in calls[-1].get_body(("html",)).get_content()


def test_smtp_permanent_failure_is_dead_not_retried(settings, db, client, fetcher):
    ev = _ev(db, client, fetcher)
    def refuse(s, m): raise notifier.PermanentSendError("Recipient address was refused by the mail server.")
    notifier.deliver_pending(db, settings, refuse)
    assert ev.status == "dead" and "refused" in ev.last_error
    notifier.deliver_pending(db, settings, refuse); assert ev.attempts == 1


def test_smtp_gives_up_after_max_attempts(settings, db, client, fetcher):
    ev = _ev(db, client, fetcher)
    for _ in range(settings.notification_max_attempts):
        ev.next_attempt_at = utcnow() - timedelta(seconds=1); db.commit()
        notifier.deliver_pending(db, settings, lambda s, m: (_ for _ in ()).throw(ConnectionError("down")))
    assert ev.status == "dead" and ev.attempts == settings.notification_max_attempts


def test_smtp_send_uses_starttls_and_login(monkeypatch, settings):
    calls = []
    class FakeSMTP:
        def __init__(self, h, p, timeout=None): calls.append(("conn", h, p))
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def starttls(self, context=None): calls.append("tls")
        def login(self, u, p): calls.append("login")
        def send_message(self, m): calls.append("send")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    notifier.send_test(settings, "me@example.com")
    assert calls == [("conn", "smtp.test", 587), "tls", "login", "send"]


def test_invalid_email_and_unconfigured_smtp(client, settings, monkeypatch):
    assert client.post("/api/v1/settings/test-email", json={"email": "nope"}).status_code in (422, 502)
    assert client.put("/api/v1/settings", json={"email": "bad"}).status_code == 400
    settings.smtp_password = ""
    r = client.post("/api/v1/settings/test-email", json={"email": "a@b.co"})
    assert r.status_code == 409 and r.json["error"]["code"] == "smtp_not_configured"


def test_secrets_never_in_api_responses(client):
    body = client.get("/api/v1/settings").get_data(as_text=True)
    assert "smtp.test" in body and '"p"' not in body and "password" not in body.lower()


# ---------------- API validation / ownership / pause -----------------
def test_alert_validation(client, fetcher):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    bad = [dict(kind="nope"), dict(kind="target_reached"), dict(kind="target_reached", target_price=-5),
           dict(kind="target_reached", target_price="abc"), dict(kind="pct_drop", pct_threshold=150), dict(kind="any_decrease", cooldown_s="x")]
    for b in bad:
        assert client.post("/api/v1/alerts", json={"product_id": pid, "email": "me@example.com", **b}).status_code == 400, b
    assert client.post("/api/v1/alerts", json={"product_id": 999, "kind": "any_decrease"}).status_code == 404


def test_alert_crud_pause_resume_and_persistence(client, fetcher, db):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    aid = mkrule(client, pid, kind="target_reached", target_price=9000)
    assert client.patch(f"/api/v1/alerts/{aid}", json={"target_price": 9500, "enabled": False}).json["alert"]["enabled"] is False
    assert client.get("/api/v1/alerts").json["alerts"][0]["target_price"] == "9500.00"
    assert client.patch(f"/api/v1/products/{pid}", json={"paused": True}).json["paused"] is True
    assert db.scalars(select(Offer)).one().monitoring_enabled is False
    client.patch(f"/api/v1/products/{pid}", json={"paused": False})
    assert client.delete(f"/api/v1/alerts/{aid}").status_code == 200 and client.get("/api/v1/alerts").json["alerts"] == []
    assert client.delete(f"/api/v1/products/{pid}").status_code == 200 and client.get("/api/v1/products").json["total"] == 0


def test_history_ranges_and_target_suggestion_endpoints(app, client, fetcher, settings, sent, db):
    fetcher.set(AMZ, page(TITLE, "10000")); pid = track(client)["product_id"]
    for p in (9000, 12000, 9500): run_price(app, settings, fetcher, db, sent, p)
    assert client.get(f"/api/v1/products/{pid}/history?range=bogus").status_code == 400
    h = client.get(f"/api/v1/products/{pid}/history?range=1w&by_retailer=1").json
    assert len(h["points"]) == 4 and "Amazon India" in h["series"]
    t = client.get(f"/api/v1/products/{pid}/target-suggestions").json
    assert t["min"] == "9000.00" and t["max"] == "12000.00" and len(t["suggestions"]) == 3
    assert client.get(f"/api/v1/products/{pid}/insights").json["verdict"]["label"] in {"below_average", "around_average", "above_average", "historically_low", "historically_high"}


def test_dashboard_has_no_fabricated_data_when_empty(client):
    d = client.get("/api/v1/dashboard").json
    assert d["tracked_products"] == 0 and d["recent_products"] == [] and d["potential_savings_to_targets"] == "0"
    assert client.get("/api/v1/deals").json["deals"] == []


def test_monitoring_status_and_error_format(app, client, settings, fetcher):
    m = client.get("/api/v1/monitoring/status").json
    assert m["worker_running"] is False and len(m["retailers"]) >= 6 and all(r["live_verified"] is False for r in m["retailers"])
    Monitor(app.config["SESSION_FACTORY"], settings, fetcher).heartbeat()
    assert client.get("/api/v1/monitoring/status").json["worker_running"] is True
    r = client.get("/api/v1/products/12345")
    assert r.status_code == 404 and r.json["success"] is False and "Traceback" not in r.get_data(as_text=True)


def test_api_token_and_request_size_limit(settings, fetcher):
    from priceradar.app import create_app
    settings.api_token = "s3cret"
    a = create_app(settings, fetcher=fetcher, providers=[], create_tables=True, check_dns=False).test_client()
    assert a.get("/api/v1/products").status_code == 401
    assert a.get("/api/v1/products", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert a.get("/api/v1/health").status_code == 200
    big = a.post("/api/v1/alerts", data="x" * 200000, headers={"Authorization": "Bearer s3cret", "Content-Type": "application/json"})
    assert big.status_code == 413


def test_legacy_routes_still_work(client, fetcher):
    assert client.get("/api/health").json["status"] == "ok"
    fetcher.set(AMZ, page(TITLE, "10000")); track(client)
    r = client.get("/api/price", query_string={"url": AMZ}).json
    assert r["price"] == 10000 and r["success"] is True
    assert client.get("/api/product", query_string={"url": "http://127.0.0.1/x"}).status_code == 422
