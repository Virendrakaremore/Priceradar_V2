"""Product identification + comparison pipeline (runs inside a Job so progress is real)."""
from __future__ import annotations

import logging
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
    """HTTP first (cheap); fall back to a rendered page when the HTTP result has no price."""
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
    cands = []
    for prov in providers:
        try:
            found, warns = prov.discover(query, ad.key)
            cands += found; warnings += warns
        except Exception as e:  # a provider must never sink the whole request
            log.warning("provider %s failed: %s", prov.name, type(e).__name__)
            warnings.append(f"Discovery provider '{prov.name}' failed.")
    progress("matching")
    for c in cands:
        try:
            add_candidate(session, settings, fetcher, product, c.retailer_key, c.url, c.provider, a, parsed.title or "")
        except (ExtractionError, UrlRejected) as e:
            warnings.append(f"{c.retailer_key}: candidate skipped ({getattr(e, 'category', getattr(e, 'code', 'error'))}).")
    session.commit()
    if not cands:
        warnings.append("No other retailers were found automatically. You can add the same product's link from another store.")
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
    mc = MatchCandidate(product_id=product.id, retailer_key=ad.key, url=parsed.canonical_url, title=parsed.title,
                        score=res.score, status=res.status, reasons=res.reasons, provider=provider)
    session.add(mc)
    if res.status == "rejected":
        session.flush(); return mc
    offer = _make_offer(session, product, ad, parsed, url, res.status, res.score, res.reasons, settings)
    if res.status == "verified":
        record_success(session, offer, parsed, settings)
    else:  # alternatives show their discovery-time price only, labelled; no history, no monitoring
        offer.current_price, offer.mrp, offer.availability = parsed.price, parsed.mrp, parsed.availability
        offer.last_success_at = offer.last_checked_at = utcnow()
        offer.status = "ok" if parsed.price else "unavailable"
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
