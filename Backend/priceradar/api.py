"""REST API (v1) plus backward-compatible legacy routes."""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from flask import Blueprint, current_app, g, jsonify, request
from sqlalchemy import func, select

from . import analytics
from .alerts import FLASH_DEFAULT_PCT
from .ingest import effective_interval, freshness
from .models import (ALERT_KINDS, AlertRule, Job, MatchCandidate, NotificationEvent, Offer, PriceObservation, Product,
                     Retailer, ScrapeFailure, Subscription, User, WorkerHeartbeat)
from .notifier import PermanentSendError, send_test, valid_email
from .retailers import ADAPTERS, adapter_for_url, allowed_domains, short_domains
from .security import UrlRejected, extract_url, resolve_redirects
from .timeutil import as_utc, iso, utcnow
from .tracking import add_candidate, ensure_retailers, get_user, identify

log = logging.getLogger("priceradar.api")
bp = Blueprint("api", __name__)


class ApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status


def err(code, message, status=400):
    raise ApiError(code, message, status)


def D(v, field, required=False, positive=True) -> Decimal | None:
    if v in (None, ""):
        if required: err("validation_error", f"{field} is required")
        return None
    try:
        d = Decimal(str(v))
    except InvalidOperation:
        err("validation_error", f"{field} must be a number")
    if not d.is_finite() or (positive and d <= 0) or d > Decimal("100000000"):
        err("validation_error", f"{field} is out of range")
    return d.quantize(Decimal("0.01"))


def money(v): return str(v) if v is not None else None
def sess(): return g.db
def cfg(): return current_app.config["SETTINGS"]


@bp.errorhandler(ApiError)
def _api_err(e):
    return jsonify({"success": False, "error": {"code": e.code, "message": e.message}}), e.status


@bp.errorhandler(UrlRejected)
def _url_err(e):
    return jsonify({"success": False, "error": {"code": e.code, "message": str(e)}}), 422


@bp.errorhandler(Exception)
def _unexpected(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"success": False, "error": {"code": e.name.lower().replace(" ", "_"), "message": e.description}}), e.code
    log.exception("unhandled error")  # stack trace goes to logs only
    return jsonify({"success": False, "error": {"code": "internal_error", "message": "Something went wrong."}}), 500


@bp.before_request
def _auth():
    token = cfg().api_token
    if token and request.endpoint not in ("api.health", "api.legacy_health"):
        if request.headers.get("Authorization", "") != f"Bearer {token}":
            err("unauthorized", "Missing or invalid API token.", 401)


# ---------------------------------------------------------------- serialisation
def offer_json(o: Offer) -> dict:
    s = cfg()
    f = freshness(o, s)
    verified = o.match_status in ("source", "verified")
    total = None
    if o.current_price is not None and o.delivery_charge is not None:
        total = o.current_price + o.delivery_charge
    disc = None
    if o.mrp and o.current_price and o.mrp > o.current_price:
        disc = {"amount": money(o.mrp - o.current_price), "pct": str(((o.mrp - o.current_price) / o.mrp * 100).quantize(Decimal("0.1")))}
    return {"id": o.id, "retailer": o.retailer.name, "retailer_key": o.retailer.key, "title": o.title, "image_url": o.image_url,
            "url": o.canonical_url, "price": money(o.current_price), "mrp": money(o.mrp), "discount": disc,
            "delivery_charge": money(o.delivery_charge), "total_payable": money(total),
            "total_note": None if total is not None else "Delivery charge not known — total payable not computed.",
            "availability": o.availability, "variant": o.variant_label, "match_status": o.match_status,
            "match_score": str(o.match_score) if o.match_score is not None else None, "match_reasons": o.match_reasons,
            "verified": verified, "status": o.status, "last_error": o.last_error,
            "last_checked_at": iso(o.last_checked_at), "last_success_at": iso(o.last_success_at),
            "monitoring_enabled": o.monitoring_enabled, "retailer_live_verified": o.retailer.live_verified,
            "confidence": str(o.confidence) if o.confidence is not None else None,
            "freshness": f, "fresh": f["fresh"]}


def best_offer(offers: list[Offer]) -> Offer | None:
    """Lowest verified, fresh, in-stock offer. Stale/unavailable/possible offers are never ranked best."""
    cands = []
    for o in offers:
        if o.match_status not in ("source", "verified") or o.current_price is None or o.availability == "out_of_stock":
            continue
        if not freshness(o, cfg())["fresh"]:
            continue
        cands.append(o)
    if not cands:
        return None
    return min(cands, key=lambda o: (o.current_price + (o.delivery_charge or 0), o.current_price))


def points_for(product_id: int, retailer_key: str | None = None, since=None) -> list[analytics.Point]:
    q = (select(PriceObservation.observed_at, PriceObservation.price, Retailer.name)
         .join(Retailer, Retailer.id == PriceObservation.retailer_id)
         .join(Offer, Offer.id == PriceObservation.offer_id)
         .where(PriceObservation.product_id == product_id, Offer.match_status.in_(("source", "verified"))))
    if retailer_key: q = q.where(Retailer.key == retailer_key)
    if since: q = q.where(PriceObservation.observed_at >= since)
    return [analytics.Point(as_utc(t), p, r) for t, p, r in sess().execute(q.order_by(PriceObservation.observed_at))]


def product_points_best(product_id: int, retailer_key=None) -> list[analytics.Point]:
    """For product-level stats, use ONE series: the source/chosen retailer, or the per-time lowest if mixed."""
    pts = points_for(product_id, retailer_key)
    if retailer_key or not pts:
        return pts
    by_r: dict[str, list[analytics.Point]] = {}
    for p in pts: by_r.setdefault(p.retailer, []).append(p)
    if len(by_r) == 1:
        return pts
    # lowest-across-retailers step series
    cur: dict[str, "Decimal"] = {}
    out = []
    for p in sorted(pts, key=lambda x: x.t):
        cur[p.retailer] = p.price
        low_r = min(cur, key=lambda r: cur[r])
        out.append(analytics.Point(p.t, cur[low_r], low_r))
    return out


def own_product(pid: int) -> Product:
    user = get_user(sess(), cfg())
    sub = sess().scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.product_id == pid)).first()
    if not sub: err("not_found", "Product not found.", 404)
    return sub.product


def product_summary(p: Product, sub: Subscription | None = None) -> dict:
    offers = sess().scalars(select(Offer).where(Offer.product_id == p.id)).all()
    best = best_offer(offers)
    src = next((o for o in offers if o.match_status == "source"), None)
    pts = product_points_best(p.id)
    st = analytics.compute_stats(pts)
    verdict = analytics.classify(st)
    rules = sess().scalars(select(AlertRule).where(AlertRule.product_id == p.id)).all()
    return {"id": p.id, "title": p.title, "brand": p.brand, "image_url": p.image_url, "paused": bool(sub and sub.paused),
            "track_until": iso(sub.track_until) if sub and sub.track_until else None,
            "created_at": iso(p.created_at), "retailers": sorted({o.retailer.name for o in offers if o.match_status != "possible"}),
            "best_offer": offer_json(best) if best else None,
            "source_offer": offer_json(src) if src else None,
            "current_price": money(best.current_price if best else (src.current_price if src else None)),
            "stats": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in st.items()},
            "verdict": verdict["label"], "alert_count": sum(1 for r in rules if r.enabled),
            "last_change_at": iso(max((o.last_change_at for o in offers if o.last_change_at), default=None))}


# ---------------------------------------------------------------- health
@bp.get("/api/v1/health")
def health():
    ok = True
    try:
        sess().execute(select(1))
    except Exception:
        ok = False
    return jsonify({"status": "ok" if ok else "degraded", "database": ok, "smtp_configured": cfg().smtp_configured}), (200 if ok else 503)


@bp.get("/api/health")
def legacy_health():
    return jsonify({"status": "ok", "message": "PriceRadar backend is running"})


# ---------------------------------------------------------------- jobs / identify
def _run_identify(app, job_id: int):
    from .tracking import identify as _identify
    with app.app_context():
        s = app.config["SESSION_FACTORY"]()
        try:
            job = s.get(Job, job_id)
            job.status = "running"; s.commit()

            def progress(stage):
                job.stage = stage; s.commit()
            product, warnings = _identify(s, app.config["SETTINGS"], app.config["FETCHER"], job.input_url,
                                          app.config["PROVIDERS"], progress, check_dns=app.config.get("CHECK_DNS", True))
            job.status, job.stage, job.product_id, job.warnings = "done", "done", product.id, warnings
            s.commit()
        except UrlRejected as e:
            s.rollback(); job = s.get(Job, job_id)
            job.status, job.stage, job.error, job.error_code = "failed", "failed", str(e), e.code; s.commit()
        except Exception:
            log.exception("identify job failed")
            s.rollback(); job = s.get(Job, job_id)
            job.status, job.stage, job.error, job.error_code = "failed", "failed", "Could not identify this product.", "internal_error"
            s.commit()
        finally:
            s.close()


def job_json(j: Job) -> dict:
    return {"id": j.id, "status": j.status, "stage": j.stage, "product_id": j.product_id, "warnings": j.warnings or [],
            "error": j.error, "error_code": j.error_code, "created_at": iso(j.created_at)}


@bp.post("/api/v1/products/identify")
def identify_start():
    body = request.get_json(silent=True) or {}
    raw = str(body.get("input") or body.get("url") or "")[:2048]
    url = extract_url(raw)                       # cheap validation before queueing
    resolved_host_ok = adapter_for_url(url) or any(url.split("/")[2].lower().endswith(d) for d in short_domains()) if "//" in url else False
    if not resolved_host_ok:
        raise UrlRejected("This retailer is not supported yet. Supported: " + ", ".join(a.name for a in ADAPTERS), "unsupported_retailer")
    job = Job(input_url=raw)
    sess().add(job); sess().commit()
    app = current_app._get_current_object()
    if cfg().run_jobs_inline:
        _run_identify(app, job.id); sess().refresh(job)
    else:
        app.config["JOB_POOL"].submit(_run_identify, app, job.id)
    return jsonify({"success": True, "job": job_json(job)}), 202


@bp.get("/api/v1/jobs/<int:jid>")
def job_status(jid):
    j = sess().get(Job, jid)
    if not j: err("not_found", "Job not found.", 404)
    return jsonify({"success": True, "job": job_json(j)})


# ---------------------------------------------------------------- products
@bp.get("/api/v1/products")
def list_products():
    user = get_user(sess(), cfg())
    q = select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.created_at.desc())
    page = max(int(request.args.get("page", 1) or 1), 1)
    per = min(max(int(request.args.get("per_page", 50) or 50), 1), 100)
    subs = sess().scalars(q).all()
    items = [product_summary(s.product, s) for s in subs]
    rf, state = request.args.get("retailer"), request.args.get("state")
    if rf: items = [i for i in items if any(rf.lower() == r.lower() for r in i["retailers"])]
    if state == "paused": items = [i for i in items if i["paused"]]
    if state == "active": items = [i for i in items if not i["paused"]]
    lo, hi = D(request.args.get("min_price"), "min_price", positive=False), D(request.args.get("max_price"), "max_price", positive=False)
    if lo is not None: items = [i for i in items if i["current_price"] and Decimal(i["current_price"]) >= lo]
    if hi is not None: items = [i for i in items if i["current_price"] and Decimal(i["current_price"]) <= hi]
    key = {"price": lambda i: Decimal(i["current_price"] or 0), "recent": lambda i: i["last_change_at"] or "",
           "alerts": lambda i: i["alert_count"], "added": lambda i: i["created_at"]}.get(request.args.get("sort", "added"))
    if key: items.sort(key=key, reverse=request.args.get("order", "desc") == "desc")
    total = len(items)
    return jsonify({"success": True, "total": total, "page": page, "per_page": per, "items": items[(page - 1) * per: page * per]})


@bp.get("/api/v1/products/<int:pid>")
def product_detail(pid):
    p = own_product(pid)
    user = get_user(sess(), cfg())
    sub = sess().scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.product_id == pid)).one()
    offers = sess().scalars(select(Offer).where(Offer.product_id == pid)).all()
    best = best_offer(offers)
    pts = product_points_best(pid)
    st = analytics.compute_stats(pts)
    verdict = analytics.classify(st)
    ojs = [offer_json(o) for o in offers]
    ojs.sort(key=lambda o: (not o["verified"], o["price"] is None, Decimal(o["price"] or 0)))
    for o in ojs: o["is_best"] = bool(best and o["id"] == best.id)
    cands = sess().scalars(select(MatchCandidate).where(MatchCandidate.product_id == pid, MatchCandidate.status == "rejected")
                           .order_by(MatchCandidate.created_at.desc()).limit(10)).all()
    return jsonify({"success": True, "product": {**product_summary(p, sub), "attributes": p.attributes, "model": p.model, "gtin": p.gtin},
                    "offers": ojs, "best_offer_id": best.id if best else None,
                    "no_fresh_offer": best is None,
                    "rejected_candidates": [{"retailer": c.retailer_key, "title": c.title, "url": c.url, "reasons": c.reasons} for c in cands],
                    "stats": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in st.items()}, "verdict": verdict,
                    "insights": analytics.build_insights(st, verdict, [{**o, "price": Decimal(o["price"]) if o["price"] else None} for o in ojs]),
                    "monitoring": {"target_interval_s": cfg().monitor_interval_s,
                                   "note": "Effective intervals are limited per retailer; see each offer's freshness."}})


@bp.delete("/api/v1/products/<int:pid>")
def delete_product(pid):
    p = own_product(pid)
    user = get_user(sess(), cfg())
    sess().query(Subscription).filter_by(user_id=user.id, product_id=pid).delete()
    sess().query(AlertRule).filter_by(user_id=user.id, product_id=pid).delete()
    if not sess().scalar(select(func.count(Subscription.id)).where(Subscription.product_id == pid)):
        sess().delete(p)
    sess().commit()
    return jsonify({"success": True})


@bp.patch("/api/v1/products/<int:pid>")
def update_product(pid):
    own_product(pid)
    user = get_user(sess(), cfg())
    body = request.get_json(silent=True) or {}
    sub = sess().scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.product_id == pid)).one()

    if "paused" in body:
        if not isinstance(body["paused"], bool):
            err("validation_error", "paused (boolean) is required")
        sub.paused = body["paused"]

    # Support track_duration_minutes (15, 30, 60, 120, 1440, 2880) or custom track_until ISO
    if "track_duration_minutes" in body:
        mins = body["track_duration_minutes"]
        if mins is None or mins == 0:
            sub.track_until = None
        else:
            try:
                sub.track_until = utcnow() + timedelta(minutes=int(mins))
                sub.paused = False # Resume tracking when setting duration
            except (ValueError, TypeError):
                err("validation_error", "track_duration_minutes must be an integer or null")
    elif "track_until" in body:
        if body["track_until"] is None:
            sub.track_until = None
        else:
            from datetime import datetime
            try:
                sub.track_until = as_utc(datetime.fromisoformat(body["track_until"].replace("Z", "+00:00")))
                sub.paused = False
            except Exception:
                err("validation_error", "track_until must be a valid ISO timestamp or null")

    for o in sess().scalars(select(Offer).where(Offer.product_id == pid, Offer.match_status.in_(("source", "verified")))):
        o.monitoring_enabled = not sub.paused
        if not sub.paused: o.next_check_at = utcnow()
    sess().commit()
    return jsonify({"success": True, "paused": sub.paused, "track_until": iso(sub.track_until) if sub.track_until else None})


@bp.post("/api/v1/products/<int:pid>/refresh")
def refresh_product(pid):
    own_product(pid)
    n = 0
    for o in sess().scalars(select(Offer).where(Offer.product_id == pid, Offer.monitoring_enabled.is_(True))):
        o.next_check_at = utcnow(); n += 1
    sess().commit()
    return jsonify({"success": True, "queued_offers": n, "note": "The monitoring worker will check these shortly."})


def _run_rediscover(app, product_id: int):
    """Background task: re-run discovery for an already-tracked product."""
    from .discovery import default_providers
    from .tracking import identify as _identify
    with app.app_context():
        s = app.config["SESSION_FACTORY"]()
        try:
            p = s.get(Product, product_id)
            if not p:
                return
            from . import matching
            src_attrs = matching.extract_attrs(p.title, p.brand, p.model, p.gtin)
            from .discovery import build_query
            query = build_query(src_attrs.brand, src_attrs.model_codes, p.title)
            # Find which retailer is the source offer so we skip it in discovery
            src_offer = s.scalars(
                select(Offer).where(Offer.product_id == product_id, Offer.match_status == "source")
            ).first()
            exclude_key = src_offer.retailer.key if src_offer else ""
            fetcher = app.config["FETCHER"]
            providers = app.config.get("PROVIDERS") or default_providers(fetcher)
            all_cands = []
            for prov in providers:
                try:
                    found, _ = prov.discover(query, exclude_key)
                    all_cands += found
                except Exception as exc:
                    log.warning("rediscover provider %s failed: %s", prov.name, exc)
            from .tracking import add_candidate
            for c in all_cands:
                try:
                    add_candidate(s, app.config["SETTINGS"], fetcher, p, c.retailer_key, c.url, c.provider,
                                  src_attrs, p.title)
                except Exception:
                    pass
            s.commit()
        except Exception:
            log.exception("rediscover job failed for product %s", product_id)
        finally:
            s.close()


@bp.post("/api/v1/products/<int:pid>/rediscover")
def rediscover_product(pid):
    """Re-run the cross-retailer discovery pipeline for an already-tracked product.
    Returns immediately; discovery runs in the background thread pool.
    """
    p = own_product(pid)
    app = current_app._get_current_object()
    app.config["JOB_POOL"].submit(_run_rediscover, app, pid)
    return jsonify({
        "success": True,
        "message": "Cross-retailer discovery started in the background. Refresh the page in ~30 seconds to see new results.",
    }), 202


@bp.post("/api/v1/products/<int:pid>/offers")
def add_offer(pid):
    p = own_product(pid)
    url = extract_url(str((request.get_json(silent=True) or {}).get("url", ""))[:2048])
    url = resolve_redirects(url, allowed_domains(), short_domains(), check_dns=current_app.config.get("CHECK_DNS", True))
    ad = adapter_for_url(url)
    if not ad: raise UrlRejected("This retailer is not supported yet.", "unsupported_retailer")
    ensure_retailers(sess())
    from .retailers import ExtractionError
    try:
        res = add_candidate(sess(), cfg(), current_app.config["FETCHER"], p, ad.key, url, "manual")
    except ExtractionError as e:
        raise UrlRejected(str(e), e.category)
    sess().commit()
    if isinstance(res, MatchCandidate):
        return jsonify({"success": False, "error": {"code": "not_same_product", "message": "That listing does not appear to be the same product.", "details": res.reasons}}), 422
    return jsonify({"success": True, "offer": offer_json(res)}), 201


@bp.get("/api/v1/products/<int:pid>/history")
def history(pid):
    own_product(pid)
    rng = request.args.get("range", "all")
    if rng not in analytics.RANGES: err("validation_error", f"range must be one of {', '.join(analytics.RANGES)}")
    now = utcnow()
    delta = analytics.RANGES[rng]
    retailer = request.args.get("retailer")
    all_pts = points_for(pid, retailer)
    pts = [p for p in all_pts if not delta or p.t >= now - delta]
    # carry the price in force at the window start so charts don't begin blank
    if delta and all_pts:
        prior = [p for p in all_pts if p.t < now - delta]
        if prior: pts = [analytics.Point(now - delta, prior[-1].price, prior[-1].retailer)] + pts
    bucket = request.args.get("bucket")
    if bucket in ("hour", "day", "week"):
        b = {"hour": timedelta(hours=1), "day": timedelta(days=1), "week": timedelta(days=7)}[bucket]
    else:
        span = (pts[-1].t - pts[0].t) if len(pts) > 1 else timedelta(0)
        b = analytics.auto_bucket(span, len(pts))
    series = {}
    if request.args.get("by_retailer") == "1":
        names = sorted({p.retailer for p in all_pts})
        for n in names:
            series[n] = [{**r, "price": str(r["price"]), "min": str(r["min"]), "max": str(r["max"])}
                         for r in analytics.bucketize([p for p in pts if p.retailer == n], b)]
    rows = analytics.bucketize(pts, b)
    st = analytics.compute_stats(pts, now) if pts else {"count": 0}
    
    # Raw recent un-bucketed samples (for the 15-second tracking live graph)
    raw_samples = [
        {"t": int(p.t.timestamp() * 1000), "price": str(p.price), "retailer": p.retailer}
        for p in pts[-50:]  # Latest 50 checks
    ]

    # Find external identifier for all-time Amazon/Flipkart history
    p_obj = sess().get(Product, pid)
    amazon_asin = None
    flipkart_pid = None
    offers = sess().scalars(select(Offer).where(Offer.product_id == pid)).all()
    for o in offers:
        if o.retailer.key == "amazon" and o.external_id:
            amazon_asin = o.external_id
        elif o.retailer.key == "flipkart" and o.external_id:
            flipkart_pid = o.external_id

    # If this is an Amazon product, provide the official all-time Keepa price history image URL
    all_time_meta = {
        "amazon_asin": amazon_asin,
        "flipkart_pid": flipkart_pid,
        "keepa_image_url": f"/api/v1/products/{pid}/all-time-graph" if amazon_asin else None,
        "has_all_time": bool(amazon_asin)
    }

    return jsonify({"success": True, "range": rng, "bucket_seconds": int(b.total_seconds()) if b else None,
                    "points": [{**r, "price": str(r["price"]), "min": str(r["min"]), "max": str(r["max"]),
                                **({"avg": str(r["avg"])} if "avg" in r else {})} for r in rows],
                    "raw_samples": raw_samples,
                    "all_time_meta": all_time_meta,
                    "series": series, "retailers": sorted({p.retailer for p in all_pts}),
                    "stats": {k: (str(v) if isinstance(v, Decimal) else v) for k, v in st.items()},
                    "provenance": "Observations collected by PriceRadar monitoring; no imported or estimated data.",
                    "insufficient": len(all_pts) < 1})


@bp.get("/api/v1/products/<int:pid>/all-time-graph")
def get_all_time_graph(pid):
    """Proxy real all-time Amazon price history chart from Keepa with caching and fallback."""
    import requests
    from flask import Response
    p = own_product(pid)
    offers = sess().scalars(select(Offer).where(Offer.product_id == pid)).all()
    asin = None
    for o in offers:
        if o.retailer.key == "amazon" and o.external_id:
            asin = o.external_id
            break
    if not asin:
        return jsonify({"error": "No Amazon ASIN available for this product"}), 404

    # Keepa price history image: amazon=1 (Amazon direct), new=1 (New 3rd party), bb=1 (BuyBox)
    keepa_url = f"https://graph.keepa.com/pricehistory.png?asin={asin}&domain=in&amazon=1&new=1&bb=1&range=0&width=860&height=320"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)",
        "Referer": "https://keepa.com/"
    }
    try:
        r = requests.get(keepa_url, headers=headers, timeout=12)
        if r.status_code == 200 and r.content[:8] == b'\x89PNG\r\n\x1a\n':
            return Response(r.content, mimetype="image/png", headers={"Cache-Control": "public, max-age=3600"})
        return jsonify({"error": f"Upstream returned status {r.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 502


@bp.get("/api/v1/products/<int:pid>/insights")
def insights(pid):
    own_product(pid)
    pts = product_points_best(pid)
    st = analytics.compute_stats(pts)
    v = analytics.classify(st)
    offers = [offer_json(o) for o in sess().scalars(select(Offer).where(Offer.product_id == pid))]
    return jsonify({"success": True, "verdict": v, "insights": analytics.build_insights(st, v, [{**o, "price": Decimal(o["price"]) if o["price"] else None} for o in offers]),
                    "stats": {k: (str(x) if isinstance(x, Decimal) else x) for k, x in st.items()}})


@bp.get("/api/v1/products/<int:pid>/target-suggestions")
def targets(pid):
    own_product(pid)
    t = analytics.target_suggestions(product_points_best(pid))
    def conv(x):
        if isinstance(x, Decimal): return str(x)
        if isinstance(x, dict): return {k: conv(v) for k, v in x.items()}
        if isinstance(x, list): return [conv(v) for v in x]
        return x
    return jsonify({"success": True, **conv(t)})


# ---------------------------------------------------------------- alerts
def alert_json(r: AlertRule) -> dict:
    p = sess().get(Product, r.product_id)
    return {"id": r.id, "product_id": r.product_id, "product_title": p.title if p else None, "offer_id": r.offer_id,
            "kind": r.kind, "target_price": money(r.target_price), "pct_threshold": str(r.pct_threshold) if r.pct_threshold is not None else None,
            "amount_threshold": money(r.amount_threshold), "min_change": money(r.min_change), "cooldown_s": r.cooldown_s,
            "enabled": r.enabled, "last_fired_at": iso(r.last_fired_at), "created_at": iso(r.created_at)}


def apply_rule_fields(r: AlertRule, body: dict, creating: bool):
    kind = body.get("kind", r.kind)
    if kind not in ALERT_KINDS: err("validation_error", f"kind must be one of {', '.join(ALERT_KINDS)}")
    r.kind = kind
    if "target_price" in body or creating: r.target_price = D(body.get("target_price"), "target_price", required=kind == "target_reached")
    if "pct_threshold" in body or creating:
        r.pct_threshold = D(body.get("pct_threshold"), "pct_threshold", required=kind == "pct_drop")
        if r.pct_threshold is not None and r.pct_threshold > 100: err("validation_error", "pct_threshold must be ≤ 100")
    if "amount_threshold" in body or creating: r.amount_threshold = D(body.get("amount_threshold"), "amount_threshold", required=kind == "amount_drop")
    if "min_change" in body: r.min_change = D(body.get("min_change"), "min_change", positive=False)
    if "cooldown_s" in body:
        try: cd = int(body["cooldown_s"])
        except (TypeError, ValueError): err("validation_error", "cooldown_s must be an integer")
        if not 0 <= cd <= 30 * 86400: err("validation_error", "cooldown_s out of range")
        r.cooldown_s = cd
    if "enabled" in body:
        if not isinstance(body["enabled"], bool): err("validation_error", "enabled must be boolean")
        r.enabled = body["enabled"]


@bp.get("/api/v1/alerts")
def list_alerts():
    user = get_user(sess(), cfg())
    q = select(AlertRule).where(AlertRule.user_id == user.id).order_by(AlertRule.created_at.desc())
    if request.args.get("product_id"): q = q.where(AlertRule.product_id == int(request.args["product_id"]))
    return jsonify({"success": True, "alerts": [alert_json(r) for r in sess().scalars(q)]})


@bp.post("/api/v1/alerts")
def create_alert():
    body = request.get_json(silent=True) or {}
    user = get_user(sess(), cfg())
    try: pid = int(body.get("product_id"))
    except (TypeError, ValueError): err("validation_error", "product_id is required")
    own_product(pid)
    email = str(body.get("email") or "").strip()
    if email:
        if not valid_email(email): err("validation_error", "Enter a valid email address.")
        user.email = email
    if not user.email: err("email_required", "Set an email address to receive alerts.")
    r = AlertRule(user_id=user.id, product_id=pid, cooldown_s=user.default_cooldown_s, state={})
    if body.get("offer_id"):
        o = sess().get(Offer, int(body["offer_id"]))
        if not o or o.product_id != pid: err("validation_error", "offer_id does not belong to this product")
        r.offer_id = o.id
    apply_rule_fields(r, body, True)
    if r.kind == "flash_drop" and r.pct_threshold is None: r.pct_threshold = FLASH_DEFAULT_PCT
    sess().add(r); sess().commit()
    return jsonify({"success": True, "alert": alert_json(r)}), 201


def own_alert(aid) -> AlertRule:
    r = sess().get(AlertRule, aid)
    if not r or r.user_id != get_user(sess(), cfg()).id: err("not_found", "Alert not found.", 404)
    return r


@bp.patch("/api/v1/alerts/<int:aid>")
def update_alert(aid):
    r = own_alert(aid)
    apply_rule_fields(r, request.get_json(silent=True) or {}, False)
    r.state = {}  # re-arm after edits
    sess().commit()
    return jsonify({"success": True, "alert": alert_json(r)})


@bp.delete("/api/v1/alerts/<int:aid>")
def delete_alert(aid):
    sess().delete(own_alert(aid)); sess().commit()
    return jsonify({"success": True})


@bp.get("/api/v1/notifications")
def notifications():
    user = get_user(sess(), cfg())
    page, per = max(int(request.args.get("page", 1)), 1), min(int(request.args.get("per_page", 25)), 100)
    q = select(NotificationEvent).where(NotificationEvent.user_id == user.id).order_by(NotificationEvent.id.desc())
    total = sess().scalar(select(func.count()).select_from(q.subquery()))
    rows = sess().scalars(q.offset((page - 1) * per).limit(per)).all()
    return jsonify({"success": True, "total": total, "page": page, "items": [
        {"id": n.id, "event_type": n.event_type, "status": n.status, "attempts": n.attempts, "error": n.last_error,
         "product_id": n.product_id, "product_name": n.payload.get("product_name"), "retailer": n.payload.get("retailer"),
         "previous_price": n.payload.get("previous_price"), "current_price": n.payload.get("current_price"),
         "reason": n.payload.get("reason"), "created_at": iso(n.created_at), "sent_at": iso(n.sent_at)} for n in rows]})


# ---------------------------------------------------------------- settings
@bp.get("/api/v1/settings")
def get_settings_route():
    u, s = get_user(sess(), cfg()), cfg()
    return jsonify({"success": True, "email": u.email, "notify_enabled": u.notify_enabled, "default_cooldown_s": u.default_cooldown_s,
                    "smtp": {"configured": s.smtp_configured, "host": s.smtp_host, "port": s.smtp_port, "tls": s.smtp_use_tls, "ssl": s.smtp_use_ssl},
                    "monitoring": {"target_interval_s": s.monitor_interval_s, "retailer_floor_s": s.retailer_min_interval_s}})


@bp.put("/api/v1/settings")
def put_settings():
    body, u = request.get_json(silent=True) or {}, get_user(sess(), cfg())
    if "email" in body:
        if body["email"] and not valid_email(str(body["email"])): err("validation_error", "Enter a valid email address.")
        u.email = str(body["email"] or "").strip()
    if "notify_enabled" in body:
        if not isinstance(body["notify_enabled"], bool): err("validation_error", "notify_enabled must be boolean")
        u.notify_enabled = body["notify_enabled"]
    if "default_cooldown_s" in body:
        try: u.default_cooldown_s = max(0, min(int(body["default_cooldown_s"]), 30 * 86400))
        except (TypeError, ValueError): err("validation_error", "default_cooldown_s must be an integer")
    sess().commit()
    return get_settings_route()


@bp.post("/api/v1/settings/test-email")
def test_email():
    u = get_user(sess(), cfg())
    to = str((request.get_json(silent=True) or {}).get("email") or u.email)
    if not cfg().smtp_configured: err("smtp_not_configured", "SMTP is not configured on the server. Set SMTP_* environment variables.", 409)
    try:
        send_test(cfg(), to, current_app.config.get("SMTP_SENDER") or __import__("priceradar.notifier", fromlist=["smtp_send"]).smtp_send)
    except PermanentSendError as e:
        err("smtp_error", str(e), 422)
    except Exception as e:
        log.warning("test email failed: %s", type(e).__name__)
        err("smtp_error", f"Could not send test email ({type(e).__name__}).", 502)
    return jsonify({"success": True, "message": f"Test email sent to {to}."})


# ---------------------------------------------------------------- dashboard / deals / monitoring
@bp.get("/api/v1/dashboard")
def dashboard():
    user = get_user(sess(), cfg())
    subs = sess().scalars(select(Subscription).where(Subscription.user_id == user.id)).all()
    items = [product_summary(s.product, s) for s in subs]
    day = utcnow() - timedelta(days=7)
    drops = []
    savings = Decimal(0)
    for it in items:
        st = it["stats"]
        ch = st.get("change_7d")
        if ch is not None and Decimal(ch) < 0:
            drops.append({"product_id": it["id"], "title": it["title"], "change": ch, "pct": st.get("change_7d_pct"), "current": it["current_price"]})
        if st.get("current") and st.get("max") and st.get("count", 0) >= 2:
            pass
    rules = sess().scalars(select(AlertRule).where(AlertRule.user_id == user.id, AlertRule.enabled.is_(True))).all()
    for r in rules:   # potential saving vs. user's own targets
        pr = next((i for i in items if i["id"] == r.product_id), None)
        if r.target_price is not None and pr and pr["current_price"] and Decimal(pr["current_price"]) > r.target_price:
            savings += Decimal(pr["current_price"]) - r.target_price
    notes = sess().scalars(select(NotificationEvent).where(NotificationEvent.user_id == user.id).order_by(NotificationEvent.id.desc()).limit(5)).all()
    recent = sess().scalars(select(Offer).where(Offer.last_success_at.is_not(None), Offer.match_status.in_(("source", "verified"))).order_by(Offer.last_success_at.desc()).limit(6)).all()
    
    # Advanced dashboard extra stats
    active_monitors = sum(1 for it in items if not it.get("paused"))
    paused_monitors = len(items) - active_monitors
    
    total_val = Decimal(0)
    lowest_drop_pct = Decimal(0)
    store_counts = {}
    
    for it in items:
        if it.get("current_price"):
            try:
                total_val += Decimal(it["current_price"])
            except Exception:
                pass
        for r in it.get("retailers", []):
            store_counts[r] = store_counts.get(r, 0) + 1
        st = it.get("stats", {})
        ch_pct = st.get("change_7d_pct")
        if ch_pct is not None:
            try:
                d_pct = Decimal(ch_pct)
                if d_pct < lowest_drop_pct:
                    lowest_drop_pct = d_pct
            except Exception:
                pass

    return jsonify({"success": True, "tracked_products": len(items), "active_alerts": len(rules),
                    "active_monitors": active_monitors, "paused_monitors": paused_monitors,
                    "portfolio_value": str(total_val), "max_savings_pct": str(abs(lowest_drop_pct)),
                    "store_counts": store_counts,
                    "confirmed_price_drops_7d": len(drops), "potential_savings_to_targets": str(savings),
                    "recent_products": items[:6], "price_drops": sorted(drops, key=lambda d: Decimal(d["change"]))[:6],
                    "recent_offers": [offer_json(o) | {"product_id": o.product_id} for o in recent],
                    "recent_notifications": [{"id": n.id, "event_type": n.event_type, "status": n.status, "product_name": n.payload.get("product_name"),
                                              "created_at": iso(n.created_at)} for n in notes],
                    "smtp_configured": cfg().smtp_configured, "email_set": bool(user.email)})


@bp.get("/api/v1/deals")
def deals():
    user = get_user(sess(), cfg())
    out = []
    for s in sess().scalars(select(Subscription).where(Subscription.user_id == user.id)):
        it = product_summary(s.product, s)
        st = it["stats"]
        if st.get("count", 0) >= 2 and st.get("pct_vs_average") is not None and Decimal(st["pct_vs_average"]) <= -3:
            out.append({"product_id": it["id"], "title": it["title"], "image_url": it["image_url"], "current": st["current"],
                        "average": st["average"], "pct_below_average": str(abs(Decimal(st["pct_vs_average"]))), "verdict": it["verdict"],
                        "retailer": it["best_offer"]["retailer"] if it["best_offer"] else None})
    out.sort(key=lambda d: -Decimal(d["pct_below_average"]))
    return jsonify({"success": True, "deals": out, "scope": "Deals are drawn from products you track and their recorded history."})


@bp.get("/api/v1/monitoring/status")
def monitoring_status():
    s, now = cfg(), utcnow()
    hbs = sess().scalars(select(WorkerHeartbeat)).all()
    workers = [{"name": h.name, "last_beat": iso(h.last_beat), "alive": (now - h.last_beat).total_seconds() < 30, "stats": h.stats} for h in hbs]
    offers = sess().scalars(select(Offer).where(Offer.match_status.in_(("source", "verified")))).all()
    rows = [{"offer_id": o.id, "product_id": o.product_id, "retailer": o.retailer.name, "title": o.title, "status": o.status,
             "monitoring_enabled": o.monitoring_enabled, "circuit_open": bool(o.circuit_open_until and o.circuit_open_until > now),
             "consecutive_failures": o.consecutive_failures, "last_error": o.last_error, "last_checked_at": iso(o.last_checked_at),
             "last_success_at": iso(o.last_success_at), **o_fresh(o)} for o in offers]
    since = now - timedelta(hours=24)
    errs = sess().execute(select(Retailer.name, ScrapeFailure.category, func.count()).join(Retailer, Retailer.id == ScrapeFailure.retailer_id)
                          .where(ScrapeFailure.created_at >= since).group_by(Retailer.name, ScrapeFailure.category)).all()
    pending = sess().scalar(select(func.count(NotificationEvent.id)).where(NotificationEvent.status.in_(("pending", "failed"))))
    return jsonify({"success": True, "worker_running": any(w["alive"] for w in workers), "workers": workers,
                    "config": {"target_interval_s": s.monitor_interval_s, "retailer_floor_s": s.retailer_min_interval_s,
                               "note": "A 15-second target is honoured only where the retailer floor allows it."},
                    "offers": rows, "retailer_errors_24h": [{"retailer": r, "category": c, "count": n} for r, c, n in errs],
                    "pending_notifications": pending,
                    "retailers": [{"key": a.key, "name": a.name, "live_verified": a.live_verified} for a in ADAPTERS]})


def o_fresh(o):
    f = freshness(o, cfg())
    return {"fresh": f["fresh"], "age_seconds": f["age_seconds"], "effective_interval_s": f["effective_interval_s"]}


@bp.get("/api/v1/retailers")
def retailers():
    return jsonify({"success": True, "retailers": [{"key": a.key, "name": a.name, "domains": list(a.domains), "live_verified": a.live_verified} for a in ADAPTERS]})


# ---------------------------------------------------------------- legacy compatibility
@bp.get("/api/expand")
def legacy_expand():
    url = resolve_redirects(extract_url(request.args.get("url", "")), allowed_domains(), short_domains(), check_dns=current_app.config.get("CHECK_DNS", True))
    return jsonify({"original": request.args.get("url", ""), "expanded": url})


@bp.get("/api/product")
def legacy_product():
    from .tracking import fetch_and_parse
    raw = request.args.get("url", "")
    url = resolve_redirects(extract_url(raw), allowed_domains(), short_domains(), check_dns=current_app.config.get("CHECK_DNS", True))
    ad = adapter_for_url(url)
    if not ad: raise UrlRejected("This retailer is not supported yet.", "unsupported_retailer")
    from .retailers import ExtractionError
    try:
        p, final = fetch_and_parse(ad, url, current_app.config["FETCHER"], cfg().use_playwright)
    except ExtractionError as e:
        return jsonify({"success": False, "error": str(e), "platform": ad.name})
    return jsonify({"success": p.price is not None, "platform": ad.name, "name": p.title, "price": int(p.price) if p.price else None,
                    "mrp": int(p.mrp) if p.mrp else None, "url": final, "currency": p.currency, "error": None if p.price else "Price not found"})


@bp.get("/api/price")
def legacy_price():
    """Deprecated: returns the stored verified price (no scraping on request). Use /api/v1."""
    raw = request.args.get("url", "")
    url = resolve_redirects(extract_url(raw), allowed_domains(), short_domains(), check_dns=current_app.config.get("CHECK_DNS", True))
    ad = adapter_for_url(url)
    o = sess().scalars(select(Offer).where(Offer.canonical_url == ad.canonical_url(url))).first() if ad else None
    if not o: return jsonify({"success": False, "price": None, "error": "Product is not tracked. POST /api/v1/products/identify first."})
    f = freshness(o, cfg())
    return jsonify({"success": o.current_price is not None and f["fresh"], "price": int(o.current_price) if o.current_price else None,
                    "platform": o.retailer.name, "timestamp": o.last_success_at.timestamp() if o.last_success_at else None,
                    "stale": not f["fresh"], "error": o.last_error})
