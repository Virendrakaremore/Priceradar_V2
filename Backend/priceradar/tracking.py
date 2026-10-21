"""Product identification + comparison pipeline (runs inside a Job so progress is real)."""
from __future__ import annotations

import logging
import concurrent.futures
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from . import matching
from .config import Settings
from .discovery import DiscoveryProvider, build_query
from .fetcher import Fetcher
from .ingest import effective_interval, record_success
from .models import MatchCandidate, Offer, Product, Retailer, Subscription, User
from .retailers import ExtractionError, adapter_by_key, adapter_for_url, allowed_domains, short_domains
from .retailers.base import ParsedOffer, RetailerAdapter
from .security import UrlRejected, extract_url, resolve_redirects
from .timeutil import utcnow

# Maximum seconds to wait for a single candidate page fetch during discovery
_CANDIDATE_TIMEOUT = 30
# How many candidate pages to fetch in parallel during the matching stage
_CANDIDATE_WORKERS = 4

log = logging.getLogger("priceradar.tracking")


def ensure_retailers(session, settings: Settings | None = None) -> None:
    from .retailers import ADAPTERS
    have = {r.key for r in session.scalars(select(Retailer))}
    for a in ADAPTERS:
        if a.key not in have:
            session.add(Retailer(key=a.key, name=a.name, domain=a.domains[0], live_verified=a.live_verified))
    session.flush()


def get_user(session, settings: Settings) -> User:
    u = session.scalars(select(User).order_by(User.id).limit(1)).first()
    if not u:
        u = User(email=settings.default_user_email or "")
        session.add(u); session.flush()
    return u


def fetch_and_parse(adapter: RetailerAdapter, url: str, fetcher: Fetcher, use_browser: bool = True) -> tuple[ParsedOffer, str]:
    """HTTP first (cheap); fall back to a rendered page when the HTTP result has no price or is blocked."""
    last: ExtractionError | None = None
    parsed = None
    for render in (False, True) if use_browser else (False,):
        try:
            res = fetcher.fetch(url, render=render)
            parsed = adapter.parse(res.html, res.final_url)
            if parsed.price is not None:
                return parsed, res.final_url
        except ExtractionError as e:
            last = e
            if e.category in ("not_found", "unsupported") or (render and e.category == "blocked"):
                raise
    if parsed is not None:
        return parsed, url        # page parsed, but no price (e.g. out of stock)
    raise last or ExtractionError("parse", "Could not read the product page.")
    if parsed is not None:
        return parsed, url        # page parsed, but no price (e.g. out of stock)
    raise last or ExtractionError("parse", "Could not read the product page.")


def _attrs(p: ParsedOffer) -> matching.Attrs:
    return matching.extract_attrs(p.title or "", p.brand, p.model, p.gtin)


def _make_offer(session, product: Product, ad: RetailerAdapter, p: ParsedOffer, original_url: str,
                status: str, score=None, reasons=None, settings: Settings | None = None) -> Offer:
    retailer = session.scalars(select(Retailer).where(Retailer.key == ad.key)).one()
    a = _attrs(p)
    offer = Offer(product_id=product.id, retailer_id=retailer.id, canonical_url=p.canonical_url, original_url=original_url[:1024],
                  external_id=p.external_id, title=(p.title or "")[:512], image_url=p.image_url,
                  variant_label=", ".join(sorted(a.storage | a.ram | a.colours | a.size)) or None,
                  match_status=status, match_score=score, match_reasons=reasons or [],
                  check_interval_s=settings.monitor_interval_s if settings else 15,
                  monitoring_enabled=status in ("source", "verified"), next_check_at=utcnow() + timedelta(seconds=1))
    offer.retailer = retailer
    session.add(offer); session.flush()
    return offer


def identify(session, settings: Settings, fetcher: Fetcher, raw_input: str, providers: list[DiscoveryProvider],
             progress=lambda stage: None, check_dns: bool = True) -> tuple[Product, list[str]]:
    warnings: list[str] = []
    progress("resolving")
    url = extract_url(raw_input)
    url = resolve_redirects(url, allowed_domains(), short_domains(), check_dns=check_dns)
    ad = adapter_for_url(url)
    if not ad:
        raise UrlRejected("This retailer is not supported yet.", "unsupported_retailer")
    ensure_retailers(session)
    user = get_user(session, settings)
    canon = ad.canonical_url(url)
    existing = session.scalars(select(Offer).where(Offer.canonical_url == canon)).first()
    if existing:  # duplicate submission -> reuse
        _subscribe(session, user, existing.product_id)
        session.commit()
        return existing.product, ["This product was already being tracked."]

    # PAUSE ALL PREVIOUS PRODUCTS for this user so only the current product is monitored actively!
    # This prevents platforms from banning the user's IP due to background requests on all old items.
    for prev_sub in session.scalars(select(Subscription).where(Subscription.user_id == user.id)):
        prev_sub.paused = True
    for prev_offer in session.scalars(
        select(Offer).join(Subscription, Subscription.product_id == Offer.product_id)
        .where(Subscription.user_id == user.id)
    ):
        prev_offer.monitoring_enabled = False

    progress("extracting")
    try:
        parsed, final = fetch_and_parse(ad, url, fetcher, settings.use_playwright)
    except ExtractionError as e:
        raise UrlRejected(_friendly(e), e.category) from e
    parsed.canonical_url = ad.canonical_url(final)
    a = _attrs(parsed)
    product = Product(title=(parsed.title or "")[:512], brand=parsed.brand, model=parsed.model, gtin=parsed.gtin,
                      image_url=parsed.image_url,
                      attributes={"storage": sorted(a.storage), "ram": sorted(a.ram), "colours": sorted(a.colours),
                                  "size": sorted(a.size), "model_codes": sorted(a.model_codes)})
    session.add(product); session.flush()
    src = _make_offer(session, product, ad, parsed, url, "source", settings=settings)
    record_success(session, src, parsed, settings)
    _subscribe(session, user, product.id)
    if parsed.price is None:
        warnings.append("The page did not show a price (possibly out of stock), so no price was recorded.")
    session.commit()

    progress("discovering")
    query = build_query(a.brand or parsed.brand, a.model_codes, parsed.title or "")
    log.info("discovery query: %r (brand=%r model_codes=%r)", query, a.brand, a.model_codes)
    cands = []
    for prov in providers:
        try:
            found, warns = prov.discover(query, ad.key)
            log.info("provider %s found %d candidates, %d warnings", prov.name, len(found), len(warns))
            cands += found
            warnings += warns
        except Exception as e:
            log.warning("provider %s failed: %s", prov.name, type(e).__name__)
            warnings.append(f"Discovery provider '{prov.name}' failed.")

    progress("matching")
    verified_count = 0
    possible_count = 0

    # Fetch and match candidates in parallel to speed up the pipeline
    def _try_candidate(c):
        try:
            return add_candidate(
                session, settings, fetcher, product,
                c.retailer_key, c.url, c.provider, a, parsed.title or "",
            )
        except (ExtractionError, UrlRejected) as e:
            cat = getattr(e, "category", getattr(e, "code", "error"))
            return cat  # signal failure with a string

    # Use threads so multiple HTTP fetches happen concurrently (DB writes are still serial)
    with concurrent.futures.ThreadPoolExecutor(max_workers=_CANDIDATE_WORKERS) as pool:
        futures = {pool.submit(_try_candidate, c): c for c in cands}
        for fut in concurrent.futures.as_completed(futures, timeout=120):
            c = futures[fut]
            try:
                result = fut.result()
            except Exception as e:
                warnings.append(f"{c.retailer_key}: candidate skipped (unexpected error: {type(e).__name__}).")
                continue
            if isinstance(result, str):
                # Error category string returned by _try_candidate
                warnings.append(f"{c.retailer_key}: candidate skipped ({result}).")
            elif isinstance(result, Offer):
                if result.match_status == "verified":
                    verified_count += 1
                elif result.match_status == "possible":
                    possible_count += 1

    session.commit()

    if not cands:
        warnings.append(
            "No other retailer listings were found automatically. "
            "You can manually add the same product's URL from another store using the 'Add retailer' button on the product page."
        )
    elif verified_count == 0 and possible_count == 0:
        warnings.append(
            f"Found {len(cands)} candidate listings across other retailers but none matched "
            "the product specification. You can manually add the correct URL from another store."
        )
    else:
        log.info("discovery complete: %d verified, %d possible out of %d candidates",
                 verified_count, possible_count, len(cands))

    return product, warnings


def add_candidate(session, settings, fetcher, product: Product, retailer_key: str, url: str, provider: str,
                  src_attrs: matching.Attrs | None = None, src_title: str | None = None) -> Offer | MatchCandidate:
    ad = adapter_by_key(retailer_key)
    if not ad:
        raise UrlRejected("Unsupported retailer.", "unsupported_retailer")
    canon = ad.canonical_url(url)
    ex = session.scalars(select(Offer).where(Offer.canonical_url == canon)).first()
    if ex:
        return ex
    parsed, final = fetch_and_parse(ad, url, fetcher, settings.use_playwright)
    parsed.canonical_url = ad.canonical_url(final)
    src_title = src_title or product.title
    src_attrs = src_attrs or matching.extract_attrs(product.title, product.brand, product.model, product.gtin)
    res = matching.compare(src_attrs, src_title, _attrs(parsed), parsed.title or "")
    log.info(
        "match %s %r → %s (score=%.3f) reasons=%s",
        retailer_key, (parsed.title or "")[:60], res.status, res.score, res.reasons,
    )
    mc = MatchCandidate(product_id=product.id, retailer_key=ad.key, url=parsed.canonical_url, title=parsed.title,
                        score=res.score, status=res.status, reasons=res.reasons, provider=provider)
    session.add(mc)
    if res.status == "rejected":
        session.flush()
        return mc
    offer = _make_offer(session, product, ad, parsed, url, res.status, res.score, res.reasons, settings)
    if res.status == "verified":
        # Fully monitored: record first price observation and schedule regular checks
        record_success(session, offer, parsed, settings)
    elif res.status == "possible":
        # "Possible" matches are shown in the comparison panel with a disclaimer.
        # We DO monitor them so the user gets live prices, but only at half the normal rate.
        offer.current_price = parsed.price
        offer.mrp = parsed.mrp
        offer.availability = parsed.availability
        offer.last_success_at = offer.last_checked_at = utcnow()
        offer.status = "ok" if parsed.price else "unavailable"
        # Monitor at reduced frequency — still useful, just not fully verified
        offer.monitoring_enabled = True
        half_interval = max((settings.monitor_interval_s or 900) * 2, 1800)
        offer.check_interval_s = half_interval
        offer.next_check_at = utcnow() + timedelta(seconds=half_interval)
    return offer


def _subscribe(session, user: User, product_id: int) -> None:
    if not session.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.product_id == product_id)).first():
        session.add(Subscription(user_id=user.id, product_id=product_id))


def _friendly(e: ExtractionError) -> str:
    return {"blocked": "The retailer blocked automated access to this page right now. Try again later.",
            "timeout": "The retailer took too long to respond.",
            "network": "Could not reach the retailer.",
            "not_found": "That product page was not found.",
            "parse": "Could not read product details from this page."}.get(e.category, str(e))
