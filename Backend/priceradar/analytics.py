"""Historical statistics, insights and target suggestions. All values come from stored observations.

Observations are stored on change (plus periodic heartbeats), so statistics are TIME-WEIGHTED:
each price holds until the next observation. Plain means of change-points would be biased.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Sequence

from .timeutil import as_utc, iso, utcnow

Q = Decimal("0.01")


@dataclass
class Point:
    t: datetime
    price: Decimal
    retailer: str | None = None


def _q(d: Decimal | None) -> Decimal | None:
    return d.quantize(Q, ROUND_HALF_UP) if d is not None else None


def _segments(points: Sequence[Point], end: datetime):
    pts = sorted(points, key=lambda p: p.t)
    for i, p in enumerate(pts):
        nxt = pts[i + 1].t if i + 1 < len(pts) else end
        yield p.price, max((nxt - p.t).total_seconds(), 0.0)


def weighted_mean_median(points: Sequence[Point], end: datetime) -> tuple[Decimal, Decimal]:
    segs = list(_segments(points, end))
    total = sum(w for _, w in segs)
    if total <= 0:  # all observations share one instant: fall back to unweighted
        vals = sorted(p.price for p in points)
        mean = sum(vals) / len(vals)
        mid = len(vals) // 2
        med = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
        return mean, med
    mean = sum(p * Decimal(str(w)) for p, w in segs) / Decimal(str(total))
    acc, med = 0.0, segs[0][0]
    for p, w in sorted(segs, key=lambda s: s[0]):
        acc += w
        if acc >= total / 2:
            med = p; break
    return mean, med


def compute_stats(points: Sequence[Point], now: datetime | None = None) -> dict:
    now = as_utc(now) or utcnow()
    if not points:
        return {"count": 0, "sufficient": False}
    pts = sorted(points, key=lambda p: p.t)
    prices = [p.price for p in pts]
    cur = pts[-1].price
    mean, med = weighted_mean_median(pts, now)
    lo, hi = min(prices), max(prices)
    lo_at = next(p.t for p in pts if p.price == lo)
    hi_at = next(p.t for p in pts if p.price == hi)
    span = (now - pts[0].t).total_seconds()
    out = {
        "count": len(pts), "current": _q(cur), "min": _q(lo), "max": _q(hi),
        "average": _q(mean), "median": _q(med), "range": _q(hi - lo),
        "min_at": iso(lo_at), "max_at": iso(hi_at),
        "first_observed_at": iso(pts[0].t), "last_observed_at": iso(pts[-1].t),
        "span_days": round(span / 86400, 2),
        # Preliminary until we have a week of history AND at least 5 observations
        "sufficient": span >= 7 * 86400 and len(pts) >= 5,
        "pct_vs_average": _q((cur - mean) / mean * 100) if mean else None,
        "pct_above_min": _q((cur - lo) / lo * 100) if lo else None,
    }
    for days in (7, 30):
        w = [p for p in pts if p.t >= now - timedelta(days=days)]
        base = None
        before = [p for p in pts if p.t < now - timedelta(days=days)]
        if before:
            base = before[-1].price      # price in force at window start
        elif w and (now - pts[0].t) >= timedelta(days=days * 0.5):
            base = w[0].price
        if base:
            out[f"change_{days}d"] = _q(cur - base)
            out[f"change_{days}d_pct"] = _q((cur - base) / base * 100)
        else:
            out[f"change_{days}d"] = out[f"change_{days}d_pct"] = None
    return out


def classify(stats: dict) -> dict:
    """Label the current price versus recorded history. Never a forecast."""
    if stats.get("count", 0) == 0:
        return {"label": "unknown", "recommendation": None, "preliminary": True,
                "explanation": "No verified price has been recorded yet."}
    cur, lo, hi, avg = stats["current"], stats["min"], stats["max"], stats["average"]
    pct = stats["pct_vs_average"] or Decimal(0)
    prelim = not stats["sufficient"]
    if stats["count"] < 2 or hi == lo:
        label = "around_average"
        why = "Only one distinct price has been recorded so far, so there is nothing to compare against."
        rec = "Not enough history yet — check back as PriceRadar records more prices."
    else:
        pos = (cur - lo) / (hi - lo)
        if cur <= lo * Decimal("1.005"):
            label = "historically_low"
        elif cur >= hi * Decimal("0.995"):
            label = "historically_high"
        elif pct <= Decimal("-3") or pos <= Decimal("0.25"):
            label = "below_average"
        elif pct >= Decimal("3") and pos >= Decimal("0.6"):
            label = "above_average"
        else:
            label = "around_average"
        why = (f"Current price ₹{cur:,.0f} sits at {int(pos*100)}% of the recorded ₹{lo:,.0f}–₹{hi:,.0f} range "
               f"and {abs(pct):.1f}% {'below' if pct < 0 else 'above'} the time-weighted average of ₹{avg:,.0f}, "
               f"based on {stats['count']} observations over {stats['span_days']} days.")
        rec = {"historically_low": "Good time to consider buying — this matches the lowest price recorded so far.",
               "below_average": "Reasonable time to consider buying — price is below its recorded average.",
               "around_average": "Price is typical for what has been recorded. Waiting may or may not help.",
               "above_average": "Consider waiting for a lower price — it is above the recorded average.",
               "historically_high": "Consider waiting — this is at the highest price recorded."}[label]
    if prelim:
        rec = "Preliminary: " + rec[0].lower() + rec[1:]
        why += " Fewer than 7 days / 5 observations are recorded, so treat this as preliminary."
    return {"label": label, "recommendation": rec, "explanation": why, "preliminary": prelim,
            "disclaimer": "Based only on recorded prices; this is not a prediction of future prices."}


def count_times_reached(points: Sequence[Point], target: Decimal) -> int:
    """Number of distinct times the price fell to/below target (down-crossings)."""
    n, below = 0, False
    for p in sorted(points, key=lambda p: p.t):
        if p.price <= target and not below:
            n += 1
        below = p.price <= target
    return n


def target_suggestions(points: Sequence[Point]) -> dict:
    if not points:
        return {"available": False, "reason": "No verified price recorded yet."}
    pts = sorted(points, key=lambda p: p.t)
    cur = pts[-1].price
    lo, hi = min(p.price for p in pts), max(p.price for p in pts)

    def build(key, label, price):
        price = _q(max(price, Decimal("1")))
        return {"key": key, "label": label, "price": price,
                "saving": _q(cur - price), "pct_drop_needed": _q((cur - price) / cur * 100) if cur else None,
                "times_reached": count_times_reached(pts, price), "reachable_by_history": price >= lo}
    if cur > lo:
        gap = cur - lo
        sugg = [build("small", "Small drop", cur - gap * Decimal("0.25")),
                build("moderate", "Moderate drop", cur - gap * Decimal("0.5")),
                build("historical_low", "Historical low", lo)]
    else:  # at/below recorded minimum: suggest percentage drops from here
        sugg = [build("small", "Small drop (−3%)", cur * Decimal("0.97")),
                build("moderate", "Moderate drop (−7%)", cur * Decimal("0.93")),
                build("historical_low", "New low (−10%)", cur * Decimal("0.90"))]
    return {"available": True, "current": _q(cur), "min": _q(lo), "max": _q(hi),
            "average": None, "slider": {"min": _q(max(lo * Decimal("0.8"), Decimal("1"))), "max": _q(max(hi, cur)), "step": 1},
            "suggestions": sugg,
            "disclaimer": "Historical minimums do not guarantee future prices. You can target below the recorded low."}


def build_insights(stats: dict, verdict: dict, offers: list[dict], now: datetime | None = None) -> list[dict]:
    out: list[dict] = []
    if stats.get("count", 0) == 0:
        return out
    cur = stats["current"]
    pa = stats["pct_vs_average"]
    if stats["count"] >= 2 and pa is not None:
        if pa <= -1:
            out.append({"kind": "vs_average", "tone": "positive", "text": f"Current price is {abs(pa):.1f}% below its recorded average."})
        elif pa >= 1:
            out.append({"kind": "vs_average", "tone": "negative", "text": f"Current price is {pa:.1f}% above its recorded average."})
    if verdict["label"] == "historically_low":
        out.append({"kind": "low", "tone": "positive", "text": "Current price matches the lowest price PriceRadar has recorded."})
    elif stats.get("pct_above_min") is not None and stats["pct_above_min"] <= 3 and stats["count"] >= 2:
        out.append({"kind": "near_low", "tone": "positive", "text": f"Current price is within {stats['pct_above_min']:.1f}% of its recorded low."})
    for d, lab in ((7, "seven"), (30, "30")):
        ch, pct = stats.get(f"change_{d}d"), stats.get(f"change_{d}d_pct")
        if ch is not None and ch != 0:
            if ch < 0:
                out.append({"kind": f"trend_{d}", "tone": "positive", "text": f"The price has fallen by ₹{abs(ch):,.0f} ({abs(pct):.1f}%) over the past {lab} days."})
            else:
                out.append({"kind": f"trend_{d}", "tone": "negative", "text": f"The price has increased by {pct:.1f}% (₹{ch:,.0f}) over the past {lab} days."})
        elif ch == 0 and stats["span_days"] >= d:
            out.append({"kind": f"stable_{d}", "tone": "neutral", "text": f"The price has remained stable for the past {lab} days."})
    best = [o for o in offers if o.get("verified") and o.get("fresh") and o.get("availability") != "out_of_stock" and o.get("price") is not None]
    if len(best) >= 2:
        best.sort(key=lambda o: o["price"])
        out.append({"kind": "lower_elsewhere", "tone": "info",
                    "text": f"{best[0]['retailer']} currently offers the lowest verified price (₹{best[0]['price']:,.0f})."})
    return out


def bucketize(points: Sequence[Point], bucket: timedelta | None) -> list[dict]:
    """Aggregate to buckets keeping min/max so extremes survive downsampling."""
    pts = sorted(points, key=lambda p: p.t)
    if not bucket:
        return [{"t": iso(p.t), "price": p.price, "min": p.price, "max": p.price, "retailer": p.retailer} for p in pts]
    out: dict[int, list[Point]] = {}
    secs = int(bucket.total_seconds())
    for p in pts:
        out.setdefault(int(p.t.timestamp()) // secs, []).append(p)
    rows = []
    for k in sorted(out):
        g = out[k]
        rows.append({"t": iso(g[-1].t), "price": g[-1].price, "min": min(x.price for x in g),
                     "max": max(x.price for x in g), "avg": _q(sum(x.price for x in g) / len(g)), "n": len(g)})
    return rows


RANGES = {"1d": timedelta(days=1), "1w": timedelta(days=7), "1m": timedelta(days=30),
          "6m": timedelta(days=182), "1y": timedelta(days=365), "all": None}


def auto_bucket(span: timedelta, n: int) -> timedelta | None:
    if n <= 400:
        return None
    for b in (timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=3), timedelta(days=1)):
        if span / b <= 600:
            return b
    return timedelta(days=1)
