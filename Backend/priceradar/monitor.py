"""Central monitoring engine. One process, bounded concurrency, DB-backed locks, no per-browser-tab loops."""
from __future__ import annotations

import logging
import signal
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import and_, or_, select, update

from .config import Settings
from .fetcher import Fetcher, close_thread_browser
from .ingest import record_failure, record_success
from .models import AlertRule, Offer, Subscription, WorkerHeartbeat
from .notifier import deliver_pending
from .retailers import ExtractionError, adapter_by_key
from .timeutil import utcnow
from .tracking import fetch_and_parse

log = logging.getLogger("priceradar.monitor")
LOCK_SECONDS = 180


class Monitor:
    def __init__(self, session_factory, settings: Settings, fetcher: Fetcher, sender=None, name: str = "worker-1"):
        self.sf, self.settings, self.fetcher, self.name = session_factory, settings, fetcher, name
        self.sender = sender
        self.stop = threading.Event()
        self._inflight: dict[str, int] = defaultdict(int)
        self._lock = threading.Lock()
        self.stats = {"checks": 0, "ok": 0, "failed": 0, "events": 0, "started_at": utcnow().isoformat()}

    # -- claiming ------------------------------------------------------------
    def claim_due(self, limit: int) -> list[int]:
        now = utcnow()
        claimed: list[int] = []
        with self.sf() as s:
            # Auto-expire subscriptions whose track_until has passed
            expired_subs = s.scalars(
                select(Subscription).where(
                    Subscription.paused.is_(False),
                    Subscription.track_until.is_not(None),
                    Subscription.track_until <= now
                )
            ).all()
            for sub in expired_subs:
                sub.paused = True
                for o in s.scalars(select(Offer).where(Offer.product_id == sub.product_id)):
                    o.monitoring_enabled = False
            if expired_subs:
                s.commit()

            q = (select(Offer).where(Offer.monitoring_enabled.is_(True),
                                     or_(Offer.next_check_at.is_(None), Offer.next_check_at <= now),
                                     or_(Offer.circuit_open_until.is_(None), Offer.circuit_open_until <= now),
                                     or_(Offer.locked_until.is_(None), Offer.locked_until <= now))
                 .order_by(Offer.next_check_at).limit(limit * 4))
            for o in s.scalars(q):
                with self._lock:
                    if self._inflight[o.retailer.key] >= self.settings.per_retailer_concurrency:
                        continue
                res = s.execute(update(Offer).where(
                    Offer.id == o.id, or_(Offer.locked_until.is_(None), Offer.locked_until <= now))
                    .values(locked_until=now + timedelta(seconds=LOCK_SECONDS)))
                if res.rowcount == 1:   # atomic claim: no overlapping jobs for the same offer
                    claimed.append(o.id)
                    with self._lock:
                        self._inflight[o.retailer.key] += 1
                if len(claimed) >= limit:
                    break
            s.commit()
        return claimed

    # -- one check -------------------------------------------------------------
    def check_offer(self, offer_id: int) -> str:
        rkey = None
        try:
            with self.sf() as s:
                offer = s.get(Offer, offer_id)
                rkey = offer.retailer.key
                ad = adapter_by_key(rkey)
                try:
                    parsed, _ = fetch_and_parse(ad, offer.canonical_url, self.fetcher, self.settings.use_playwright)
                    out = record_success(s, offer, parsed, self.settings)
                    self.stats["ok"] += 1
                    self.stats["events"] += len(out.get("events", []))
                    status = out.get("status", "ok")
                except ExtractionError as e:
                    record_failure(s, offer, e.category, str(e), e.retryable, self.settings)
                    self.stats["failed"] += 1
                    status = "failed:" + e.category
                except Exception as e:  # never let one offer kill the worker
                    log.exception("unexpected error checking offer %s", offer_id)
                    record_failure(s, offer, "network", f"Unexpected {type(e).__name__}", True, self.settings)
                    self.stats["failed"] += 1
                    status = "failed:unexpected"
                s.commit()
                self.stats["checks"] += 1
                return status
        finally:
            if rkey:
                with self._lock:
                    self._inflight[rkey] = max(0, self._inflight[rkey] - 1)

    def release_locks(self):
        with self.sf() as s:
            s.execute(update(Offer).where(Offer.locked_until.is_not(None)).values(locked_until=None))
            s.commit()

    def heartbeat(self):
        with self.sf() as s:
            hb = s.get(WorkerHeartbeat, self.name) or WorkerHeartbeat(name=self.name)
            hb.last_beat, hb.stats = utcnow(), dict(self.stats)
            s.merge(hb); s.commit()

    def tick(self, pool: ThreadPoolExecutor | None = None) -> int:
        ids = self.claim_due(self.settings.worker_concurrency)
        if pool:
            for i in ids:
                pool.submit(self.check_offer, i)
        else:
            for i in ids:
                self.check_offer(i)
        with self.sf() as s:
            deliver_pending(s, self.settings, *(() if self.sender is None else (self.sender,)))
        self.heartbeat()
        return len(ids)

    def run_forever(self):
        log.info("Monitor %s starting (concurrency=%s)", self.name, self.settings.worker_concurrency)
        self.release_locks()  # recover from an unclean shutdown
        pool = ThreadPoolExecutor(max_workers=self.settings.worker_concurrency, thread_name_prefix="check",
                                  initializer=None)
        while not self.stop.is_set():
            try:
                n = self.tick(pool)
            except Exception:
                log.exception("monitor tick failed"); n = 0
            self.stop.wait(1.0 if n else 2.0)
        log.info("Shutting down: waiting for in-flight checks")
        pool.shutdown(wait=True)
        self.release_locks()
        self.heartbeat()

    def install_signal_handlers(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: self.stop.set())
