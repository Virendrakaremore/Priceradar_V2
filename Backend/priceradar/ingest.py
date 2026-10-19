"""Turn a check outcome into persisted state: observations, offer status, scheduling, alert events."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from .alerts import evaluate_rules
from .config import Settings
from .models import Offer, PriceObservation, ScrapeFailure
from .retailers.base import ParsedOffer
from .timeutil import utcnow


def effective_interval(offer: Offer, settings: Settings) -> int:
    floor = offer.retailer.min_interval_s or settings.retailer_min_interval_s
    return max(offer.check_interval_s or settings.monitor_interval_s, floor)


def _schedule_ok(offer: Offer, settings: Settings, now):
    offer.next_check_at = now + timedelta(seconds=effective_interval(offer, settings))


def record_success(session, offer: Offer, parsed: ParsedOffer, settings: Settings, source: str = "scrape") -> dict:
    now = utcnow()
    offer.last_checked_at = now
    offer.locked_until = None
    prev_price, prev_avail = offer.current_price, offer.availability
    result = {"accepted": False, "events": [], "pending_confirmation": False}

    if parsed.price is None:
        # Page loaded but no price: out of stock listings legitimately have none. Do NOT record 0.
        offer.status = "unavailable"
        offer.availability = parsed.availability if parsed.availability != "unknown" else offer.availability
        offer.last_error = "No price shown on the product page."
        offer.consecutive_failures = 0
        _schedule_ok(offer, settings, now)
        result["status"] = "unavailable"
        if prev_avail != offer.availability:
            result["events"] = evaluate_rules(session, offer, prev_price, None, prev_avail, offer.availability, new_obs_recorded=False)
        return result

    price = parsed.price
    # Confirmation gate for implausible jumps: need two consecutive identical readings.
    if prev_price and abs(price - prev_price) / prev_price * 100 >= settings.big_jump_pct:
        if offer.pending_price != price:
            offer.pending_price = price
            offer.next_check_at = now + timedelta(seconds=5)
            offer.status = "ok" if offer.last_success_at else "pending"
            result.update(status="pending_confirmation", pending_confirmation=True)
            return result
    offer.pending_price = None

    offer.status, offer.last_error, offer.consecutive_failures = "ok", None, 0
    offer.circuit_open_until = None
    offer.last_success_at = now
    offer.mrp = parsed.mrp
    offer.delivery_charge = parsed.delivery_charge if parsed.delivery_charge is not None else offer.delivery_charge
    offer.availability = parsed.availability
    offer.currency = parsed.currency
    offer.confidence = parsed.confidence
    if parsed.image_url and not offer.image_url:
        offer.image_url = parsed.image_url

    last_obs = session.scalars(select(PriceObservation).where(PriceObservation.offer_id == offer.id)
                               .order_by(PriceObservation.observed_at.desc()).limit(1)).first()
    changed = last_obs is None or last_obs.price != price or last_obs.availability != parsed.availability
    heartbeat = last_obs is not None and (now - last_obs.observed_at).total_seconds() >= settings.heartbeat_sample_s
    if changed or heartbeat:
        session.add(PriceObservation(
            offer_id=offer.id, product_id=offer.product_id, retailer_id=offer.retailer_id,
            variant_label=offer.variant_label, price=price, mrp=parsed.mrp, availability=parsed.availability,
            currency=parsed.currency, observed_at=now, status="verified", source=parsed.source or source,
            confidence=parsed.confidence, meta={"heartbeat": bool(heartbeat and not changed)}))
    if prev_price != price:
        offer.last_change_at = now
    offer.current_price = price
    _schedule_ok(offer, settings, now)
    session.flush()
    result.update(accepted=True, status="ok", changed=prev_price != price)
    if prev_price is not None or prev_avail != parsed.availability:
        result["events"] = evaluate_rules(session, offer, prev_price, price, prev_avail, parsed.availability,
                                          new_obs_recorded=changed)
    return result


def record_failure(session, offer: Offer, category: str, message: str, retryable: bool, settings: Settings) -> dict:
    """Failures never touch current_price: the last verified value stays, flagged stale by freshness logic."""
    now = utcnow()
    offer.last_checked_at, offer.locked_until = now, None
    offer.consecutive_failures = (offer.consecutive_failures or 0) + 1
    offer.last_error = message[:500]
    offer.status = "blocked" if category == "blocked" else ("error" if offer.last_success_at is None else "stale")
    session.add(ScrapeFailure(offer_id=offer.id, retailer_id=offer.retailer_id, category=category,
                              message=message[:500], retryable=retryable))
    n = offer.consecutive_failures
    backoff = min(settings.backoff_base_s * (2 ** (n - 1)), settings.backoff_max_s)
    offer.next_check_at = now + timedelta(seconds=backoff)
    opened = False
    if not retryable:
        offer.monitoring_enabled = False
        offer.status = "unavailable" if category == "not_found" else offer.status
    elif n >= settings.circuit_threshold:
        offer.circuit_open_until = now + timedelta(seconds=settings.circuit_cooldown_s)
        offer.next_check_at = offer.circuit_open_until
        opened = True
    return {"status": offer.status, "backoff_s": backoff, "circuit_open": opened}


def freshness(offer: Offer, settings: Settings, now=None) -> dict:
    now = now or utcnow()
    interval = effective_interval(offer, settings)
    age = (now - offer.last_success_at).total_seconds() if offer.last_success_at else None
    limit = max(interval * settings.stale_after_multiplier, 300)
    fresh = age is not None and age <= limit and offer.status in ("ok", "pending")
    return {"fresh": bool(fresh), "age_seconds": int(age) if age is not None else None,
            "effective_interval_s": interval, "requested_interval_s": offer.check_interval_s,
            "limited_by_retailer_floor": interval > (offer.check_interval_s or 0)}
