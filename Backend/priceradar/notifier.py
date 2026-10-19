"""SMTP notifications: HTML + text emails, delivery tracking, bounded retries."""
from __future__ import annotations

import html
import logging
import re
import smtplib
import ssl
from datetime import timedelta
from decimal import Decimal
from email.message import EmailMessage
from email.utils import formataddr

from sqlalchemy import or_, select

from .config import Settings
from .models import NotificationEvent
from .timeutil import utcnow

log = logging.getLogger("priceradar.notify")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")
TITLES = {"target_reached": "Target price reached", "price_drop": "Price dropped", "price_increase": "Price increased",
          "pct_drop": "Percentage drop reached", "amount_drop": "Price drop threshold reached",
          "flash_drop": "Sudden price drop", "historical_low": "New historical low",
          "back_in_stock": "Back in stock", "lower_elsewhere": "Lower price found at another retailer",
          "test": "PriceRadar test email"}


def valid_email(addr: str) -> bool:
    return bool(addr and len(addr) <= 254 and EMAIL_RE.match(addr))


def _money(v) -> str:
    try:
        return "₹{:,.0f}".format(Decimal(str(v))) if Decimal(str(v)) % 1 == 0 else "₹{:,.2f}".format(Decimal(str(v)))
    except Exception:
        return "—"


def build_message(event_type: str, p: dict, settings: Settings, to: str) -> EmailMessage:
    e = html.escape
    cur, prev = p.get("current_price"), p.get("previous_price")
    saving = pct = None
    if cur and prev:
        d = Decimal(prev) - Decimal(cur)
        saving, pct = d, (d / Decimal(prev) * 100 if Decimal(prev) else None)
    title = TITLES.get(event_type, "PriceRadar alert")
    retailer = p.get("retailer") or "the retailer"
    name = p.get("product_name") or "Tracked product"
    track_url = f"{settings.public_app_url.rstrip('/')}/products/{p.get('product_id')}" if p.get("product_id") else settings.public_app_url
    subject = f"PriceRadar: {title} — {name[:60]}" + (f" at {_money(cur)}" if cur else "")

    def cell(label, value, strong=False):
        return (f'<td style="padding:14px;background:#1b1b1d;border:1px solid #2c2c2f;border-radius:10px;text-align:center">'
                f'<div style="font-size:11px;letter-spacing:.08em;color:#8a8a90;text-transform:uppercase">{e(label)}</div>'
                f'<div style="font-size:{26 if strong else 18}px;font-weight:700;color:#f5f5f5;margin-top:4px">{e(value)}</div></td>')
    cells = [cell("Previous", _money(prev)) if prev else "", cell("Now", _money(cur), True) if cur else ""]
    if p.get("target_price"):
        cells.append(cell("Your target", _money(p["target_price"])))
    img = (f'<img src="{e(p["product_image"], quote=True)}" alt="" width="96" style="border-radius:10px;margin-right:14px;float:left;background:#fff">'
           if str(p.get("product_image") or "").startswith("https://") else "")
    offer_url = e(str(p.get("offer_url") or "#"), quote=True)
    savings_line = (f'<p style="color:#d8d8dc;margin:14px 0 0">{"Saving" if saving > 0 else "Change"}: <b>{_money(abs(saving))}</b>'
                    f'{f" ({abs(pct):.1f}%)" if pct is not None else ""}</p>' if saving is not None else "")
    body = f"""<!doctype html><html><body style="margin:0;background:#0b0b0c;font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">
<div style="max-width:600px;margin:0 auto;padding:28px 16px"><div style="background:#141415;border:1px solid #2c2c2f;border-radius:16px;padding:28px">
<div style="font-size:13px;letter-spacing:.18em;color:#9a9aa0;text-transform:uppercase">◉ PriceRadar</div>
<h1 style="color:#fff;font-size:22px;margin:10px 0 18px">{e(title)}</h1>
<div style="overflow:hidden;margin-bottom:18px">{img}<div style="color:#f0f0f2;font-size:16px;line-height:1.4">{e(name)}</div>
<div style="color:#8a8a90;font-size:13px;margin-top:6px">{e(retailer)}</div></div>
<table role="presentation" width="100%" cellspacing="8" style="border-collapse:separate"><tr>{''.join(cells)}</tr></table>
{savings_line}
<p style="color:#b8b8be;font-size:14px;line-height:1.5;margin:16px 0 0"><b>Why you got this:</b> {e(p.get('reason') or '')}</p>
<p style="margin:22px 0 8px"><a href="{offer_url}" style="display:inline-block;background:#f5f5f5;color:#0b0b0c;padding:13px 22px;border-radius:10px;text-decoration:none;font-weight:600">View on {e(retailer)}</a>
<a href="{e(track_url, quote=True)}" style="display:inline-block;color:#d8d8dc;padding:13px 14px;text-decoration:none">Open in PriceRadar</a></p>
<p style="color:#6f6f75;font-size:12px;margin-top:22px">Detected {e(p.get('detected_at') or '')}. Prices can change quickly and may differ at checkout.</p>
</div></div></body></html>"""
    text = "\n".join(filter(None, [
        f"PriceRadar — {title}", "", name, f"Retailer: {retailer}",
        f"Previous: {_money(prev)}" if prev else "", f"Now: {_money(cur)}" if cur else "",
        f"Your target: {_money(p['target_price'])}" if p.get("target_price") else "",
        f"Saving: {_money(abs(saving))}" if saving is not None else "",
        "", f"Why: {p.get('reason') or ''}", f"Detected: {p.get('detected_at') or ''}", "",
        f"View on {retailer}: {p.get('offer_url')}", f"Open in PriceRadar: {track_url}"]))
    msg = EmailMessage()
    msg["Subject"], msg["To"] = subject, to
    msg["From"] = formataddr(("PriceRadar", settings.sender))
    msg.set_content(text)
    msg.add_alternative(body, subtype="html")
    return msg


class PermanentSendError(Exception):
    pass


def smtp_send(settings: Settings, msg: EmailMessage) -> None:
    if not settings.smtp_configured:
        raise PermanentSendError("SMTP is not configured.")
    try:
        if settings.smtp_use_ssl:
            srv = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout,
                                   context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout)
        with srv:
            if settings.smtp_use_tls and not settings.smtp_use_ssl:
                srv.starttls(context=ssl.create_default_context())
            srv.login(settings.smtp_username, settings.smtp_password)
            srv.send_message(msg)
    except smtplib.SMTPRecipientsRefused as e:
        raise PermanentSendError("Recipient address was refused by the mail server.") from e
    except smtplib.SMTPAuthenticationError as e:
        raise PermanentSendError("SMTP authentication failed — check username/app password.") from e


def send_test(settings: Settings, to: str, sender=smtp_send) -> None:
    if not valid_email(to):
        raise PermanentSendError("Invalid recipient address.")
    p = {"product_name": "Sample product (test email)", "retailer": "PriceRadar", "previous_price": "12000",
         "current_price": "9999", "target_price": "10000", "offer_url": settings.public_app_url,
         "reason": "This is a test message confirming your SMTP settings work.", "detected_at": utcnow().isoformat()}
    sender(settings, build_message("target_reached", p, settings, to))


def deliver_pending(session, settings: Settings, sender=smtp_send, limit: int = 20) -> dict:
    now = utcnow()
    evs = session.scalars(select(NotificationEvent).where(
        NotificationEvent.status.in_(("pending", "failed")),
        or_(NotificationEvent.next_attempt_at.is_(None), NotificationEvent.next_attempt_at <= now),
        NotificationEvent.attempts < settings.notification_max_attempts).order_by(NotificationEvent.id).limit(limit)).all()
    stats = {"sent": 0, "failed": 0, "dead": 0}
    for ev in evs:
        ev.attempts += 1
        try:
            if not valid_email(ev.recipient):
                raise PermanentSendError("Invalid recipient address.")
            sender(settings, build_message(ev.event_type, ev.payload, settings, ev.recipient))
            ev.status, ev.sent_at, ev.last_error = "sent", utcnow(), None
            stats["sent"] += 1
        except PermanentSendError as e:
            ev.status, ev.last_error = "dead", str(e)[:500]
            stats["dead"] += 1
        except Exception as e:  # timeouts, connection resets, 4xx -> retry with backoff
            ev.last_error = f"{type(e).__name__}: {e}"[:500]
            if ev.attempts >= settings.notification_max_attempts:
                ev.status = "dead"; stats["dead"] += 1
            else:
                ev.status = "failed"
                ev.next_attempt_at = now + timedelta(seconds=min(60 * 2 ** (ev.attempts - 1), 3600))
                stats["failed"] += 1
        session.commit()
    return stats
