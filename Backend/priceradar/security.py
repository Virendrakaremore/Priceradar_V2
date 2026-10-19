"""URL extraction + SSRF-safe validation and redirect resolution.

Only hosts belonging to registered retailers (or their known short-link domains) are ever fetched.
Every redirect hop is re-validated, and hostnames must resolve to public IP addresses only.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import urlparse, urlunparse

import requests

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
MAX_HOPS = 5
UA = "PriceRadarBot/2.0 (+price comparison; contact via PUBLIC_APP_URL)"


class UrlRejected(ValueError):
    def __init__(self, message: str, code: str = "invalid_url"):
        super().__init__(message)
        self.code = code


def extract_url(text: str) -> str:
    """Pull the first URL out of pasted share text (e.g. a WhatsApp message)."""
    if not text or not text.strip():
        raise UrlRejected("Paste a product link.", "empty")
    m = URL_RE.search(text.strip())
    if not m:
        raise UrlRejected("No http(s) link found in the pasted text.", "no_url")
    return m.group(0).rstrip(".,);]")


def host_allowed(host: str, allowed_domains: set[str]) -> bool:
    host = (host or "").lower().rstrip(".")
    return any(host == d or host.endswith("." + d) for d in allowed_domains)


def _is_public_ip(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return not (a.is_private or a.is_loopback or a.is_link_local or a.is_multicast
                or a.is_reserved or a.is_unspecified)


def assert_public_host(host: str) -> None:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise UrlRejected(f"Could not resolve {host}.", "dns") from e
    for info in infos:
        if not _is_public_ip(info[4][0]):
            raise UrlRejected("URL resolves to a non-public address.", "ssrf_blocked")


def validate_url(url: str, allowed_domains: set[str], check_dns: bool = True) -> str:
    p = urlparse(url)
    if p.scheme not in ("http", "https"):
        raise UrlRejected("Only http/https links are supported.")
    if not p.hostname or p.username or p.password:
        raise UrlRejected("Invalid link.")
    try:  # literal IPs are never acceptable
        ipaddress.ip_address(p.hostname)
        raise UrlRejected("IP-address links are not allowed.", "ssrf_blocked")
    except ValueError as e:
        if isinstance(e, UrlRejected):
            raise
    if p.port not in (None, 80, 443):
        raise UrlRejected("Non-standard ports are not allowed.", "ssrf_blocked")
    if not host_allowed(p.hostname, allowed_domains):
        raise UrlRejected("This retailer is not supported yet.", "unsupported_retailer")
    if check_dns:
        assert_public_host(p.hostname)
    return urlunparse(p._replace(scheme="https", fragment=""))


def resolve_redirects(url: str, allowed_domains: set[str], short_domains: set[str],
                      session: requests.Session | None = None, check_dns: bool = True) -> str:
    """Follow redirects manually, validating each hop. Short links are the only ones followed;
    full retailer URLs are returned untouched (no needless network call)."""
    url = validate_url(url, allowed_domains | short_domains, check_dns)
    sess = session or requests.Session()
    for _ in range(MAX_HOPS):
        host = urlparse(url).hostname or ""
        if not host_allowed(host, short_domains):
            return url
        try:
            r = sess.get(url, allow_redirects=False, timeout=10, stream=True,
                         headers={"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"})
        except requests.RequestException as e:
            raise UrlRejected("Could not expand the short link.", "expand_failed") from e
        loc = r.headers.get("Location")
        r.close()
        if r.status_code in (301, 302, 303, 307, 308) and loc:
            nxt = requests.compat.urljoin(url, loc)
            url = validate_url(nxt, allowed_domains | short_domains, check_dns)
            continue
        return url
    raise UrlRejected("Too many redirects.", "too_many_redirects")
