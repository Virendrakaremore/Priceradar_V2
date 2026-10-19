"""Alert rule evaluation. Produces de-duplicated NotificationEvents; never fires on failed/unverified data."""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .models import AlertRule, NotificationEvent, Offer, PriceObservation, Product, User
from .timeutil import iso, utcnow

FLASH_WINDOW = timedelta(minutes=15)
FLASH_DEFAULT_PCT = Decimal("10")


def _event(session, rule: AlertRule, user: User, offer: Offer, event_type: str, key: str, payload: dict) -> NotificationEvent | None:
    ev = NotificationEvent(rule_id=rule.id, user_id=user.id, product_id=offer.product_id, offer_id=offer.id,
                           event_type=event_type, event_key=key[:128], payload=payload, recipient=user.email,
                           status="pending", next_attempt_at=utcnow())
    try:
        with session.begin_nested():
            session.add(ev)
            session.flush()
    except IntegrityError:  # same (rule, key) already recorded -> duplicate suppressed
        return None
    rule.last_fired_at = utcnow()
    return ev


def _payload(offer: Offer, prev: Decimal | None, cur: Decimal | None, rule: AlertRule, reason: str) -> dict:
    p: Product = offer.product
    return {"product_name": p.title, "product_image": offer.image_url or p.image_url,
            "retailer": offer.retailer.name, "offer_url": offer.canonical_url,
            "previous_price": str(prev) if prev is not None else None,
            "current_price": str(cur) if cur is not None else None,
            "target_price": str(rule.target_price) if rule.target_price is not None else None,
            "mrp": str(offer.mrp) if offer.mrp is not None else None,
            "availability": offer.availability, "detected_at": iso(utcnow()),
            "product_id": p.id, "reason": reason}


def evaluate_rules(session, offer: Offer, prev_price: Decimal | None, new_price: Decimal | None,
                   prev_avail: str, new_avail: str, *, new_obs_recorded: bool) -> list[NotificationEvent]:
    """Called after a verified check. ``prev_price`` is the previous verified price for this offer."""
    now = utcnow()
    rules = session.scalars(select(AlertRule).where(
        AlertRule.product_id == offer.product_id, AlertRule.enabled.is_(True),
        (AlertRule.offer_id.is_(None)) | (AlertRule.offer_id == offer.id))).all()
    events: list[NotificationEvent] = []
    if offer.match_status == "possible":
        return events  # alternatives never trigger alerts
    for rule in rules:
        user = session.get(User, rule.user_id)
        if not user or not user.notify_enabled or not user.email:
            continue
        st = dict(rule.state or {})
        cooled = not rule.last_fired_at or (now - rule.last_fired_at) >= timedelta(seconds=rule.cooldown_s)
        kind = rule.kind
        changed = new_price is not None and prev_price is not None and new_price != prev_price
        delta = (prev_price - new_price) if changed else Decimal(0)  # +ve = drop
        min_ok = (not rule.min_change) or abs(delta) >= rule.min_change
        ev = None
        if kind == "target_reached" and new_price is not None and rule.target_price is not None:
            key_epoch = int(st.get(f"epoch:{offer.id}", 0))
            if new_price > rule.target_price:
                if not st.get(f"armed:{offer.id}", True):
                    st[f"armed:{offer.id}"] = True
                    st[f"epoch:{offer.id}"] = key_epoch + 1  # next crossing is a distinct event
            elif st.get(f"armed:{offer.id}", True) and cooled:
                ev = _event(session, rule, user, offer, "target_reached", f"target:{offer.id}:{key_epoch}",
                            _payload(offer, prev_price, new_price, rule,
                                     f"The price (₹{new_price:,.2f}) is at or below your target of ₹{rule.target_price:,.2f}."))
                if ev:
                    st[f"armed:{offer.id}"] = False
        elif kind == "any_decrease" and changed and delta > 0 and min_ok and cooled:
            ev = _event(session, rule, user, offer, "price_drop", f"dec:{offer.id}:{new_price}:{int(now.timestamp())}",
                        _payload(offer, prev_price, new_price, rule, f"Price decreased by ₹{delta:,.2f}."))
        elif kind == "any_increase" and changed and delta < 0 and min_ok and cooled:
            ev = _event(session, rule, user, offer, "price_increase", f"inc:{offer.id}:{new_price}:{int(now.timestamp())}",
                        _payload(offer, prev_price, new_price, rule, f"Price increased by ₹{-delta:,.2f}."))
        elif kind == "pct_drop" and changed and delta > 0 and rule.pct_threshold is not None and cooled:
            pct = delta / prev_price * 100
            if pct >= rule.pct_threshold and min_ok:
                ev = _event(session, rule, user, offer, "pct_drop", f"pct:{offer.id}:{new_price}:{int(now.timestamp())}",
                            _payload(offer, prev_price, new_price, rule, f"Price fell {pct:.1f}% (threshold {rule.pct_threshold}%)."))
        elif kind == "amount_drop" and changed and delta > 0 and rule.amount_threshold is not None and cooled:
            if delta >= rule.amount_threshold:
                ev = _event(session, rule, user, offer, "amount_drop", f"amt:{offer.id}:{new_price}:{int(now.timestamp())}",
                            _payload(offer, prev_price, new_price, rule, f"Price fell by ₹{delta:,.2f} (threshold ₹{rule.amount_threshold:,.2f})."))
        elif kind == "flash_drop" and changed and delta > 0 and cooled:
            thr = rule.pct_threshold or FLASH_DEFAULT_PCT
            recent_max = session.scalar(select(func.max(PriceObservation.price)).where(
                PriceObservation.offer_id == offer.id, PriceObservation.observed_at >= now - FLASH_WINDOW))
            ref = max(recent_max or prev_price, prev_price)
            pct = (ref - new_price) / ref * 100
            if pct >= thr:
                ev = _event(session, rule, user, offer, "flash_drop", f"flash:{offer.id}:{new_price}:{int(now.timestamp())//900}",
                            _payload(offer, ref, new_price, rule, f"Sudden {pct:.1f}% drop within {int(FLASH_WINDOW.total_seconds()//60)} minutes."))
        elif kind == "historical_low" and changed and delta > 0 and cooled and new_obs_recorded:
            hist = session.execute(select(func.min(PriceObservation.price), func.count(PriceObservation.id)).where(
                PriceObservation.offer_id == offer.id)).one()
            # min is computed incl. the new row; "new low" = strictly below every earlier price
            prior_min = session.scalar(select(func.min(PriceObservation.price)).where(
                PriceObservation.offer_id == offer.id, PriceObservation.price != new_price)) if hist[1] else None
            if hist[1] >= 3 and prior_min is not None and new_price < prior_min:
                ev = _event(session, rule, user, offer, "historical_low", f"low:{offer.id}:{new_price}",
                            _payload(offer, prior_min, new_price, rule, f"New lowest price recorded (previous low ₹{prior_min:,.2f})."))
        elif kind == "back_in_stock" and prev_avail == "out_of_stock" and new_avail == "in_stock" and cooled:
            ev = _event(session, rule, user, offer, "back_in_stock", f"stock:{offer.id}:{int(now.timestamp())}",
                        _payload(offer, prev_price, new_price, rule, "The product is available again."))
        elif kind == "lower_elsewhere" and new_price is not None and changed and delta > 0 and cooled:
            others = session.scalars(select(Offer).where(
                Offer.product_id == offer.product_id, Offer.id != offer.id, Offer.match_status.in_(("source", "verified")),
                Offer.current_price.is_not(None), Offer.availability != "out_of_stock")).all()
            if others and new_price < min(o.current_price for o in others):
                ev = _event(session, rule, user, offer, "lower_elsewhere", f"lowest:{offer.id}:{new_price}",
                            _payload(offer, min(o.current_price for o in others), new_price, rule,
                                     f"{offer.retailer.name} is now the lowest verified price across tracked retailers."))
        rule.state = st
        if ev:
            events.append(ev)
    return events
