"""Conservative product matching. A 'verified' match needs hard evidence; similar words alone
are not enough.

Indian e-commerce titles differ significantly across retailers:
  Amazon:   "Samsung Galaxy S24 Ultra 5G (Titanium Black, 12GB+256GB)"
  Flipkart: "SAMSUNG Galaxy S24 Ultra 5G (Titanium Black, 256 GB)(12 GB RAM)"
  Croma:    "Samsung Galaxy S24 Ultra 5G Smartphone (12GB RAM, 256GB, Titanium Black)"

The thresholds here are tuned to handle these variations while still rejecting genuinely
different products (different model numbers, different storage configurations, etc.).

Changes from v1:
  * model_overlap sim threshold lowered to 0.35 (was 0.45) — handles abbreviated retailer titles
  * title-only similarity threshold lowered to 0.60 (was 0.70) for "possible" matches
  * brand normalisation strips noise suffixes ("india", "official", "ltd" …)
  * partial model-code containment ("s24ultra" matches "s24-ultra")
  * colour mismatch => "possible" instead of early rejection
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

COLOURS = {
    "black", "white", "blue", "red", "green", "silver", "gold", "grey", "gray", "pink",
    "purple", "yellow", "orange", "titanium", "graphite", "midnight", "starlight", "violet",
    "lavender", "cream", "beige", "brown", "navy", "teal", "natural", "desert", "phantom",
    "cosmic", "mystic", "prism", "coral", "sage", "mint", "slate", "rose", "glacier",
    "arctic", "space", "dark", "light", "pearl", "olive", "charcoal",
}
BUNDLE_WORDS = {"combo", "bundle", "pack of", "set of", "with free", "kit"}
STOP = {
    "the", "and", "with", "for", "new", "latest", "buy", "online", "india", "in", "by",
    "of", "a", "smartphone", "mobile", "phone", "5g", "4g", "lte", "wifi", "bt",
    "bluetooth", "android", "ios", "laptop", "tablet", "monitor", "tv", "television",
}

_STORAGE = re.compile(r"(\d+)\s?(gb|tb)\b", re.I)
_RAM     = re.compile(r"(\d+)\s?gb\s*(?:ram|memory)\b|\bram\s*(\d+)\s?gb", re.I)
_SIZE    = re.compile(r'(\d{2,3}(?:\.\d)?)[\s-]?(?:inch|inches|cm)\b', re.I)
_MODEL   = re.compile(r"\b(?=[A-Z0-9\-]*\d)(?=[A-Z0-9\-]*[A-Z])[A-Z][A-Z0-9\-]{3,}\b")
_BRAND_NOISE = re.compile(
    r"\s*(india|official|ltd|limited|electronics|tech|technologies|pvt)\s*$", re.I
)


def normalize(title: str) -> str:
    t = re.sub(r"[^a-z0-9. ]+", " ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def tokens(title: str) -> set[str]:
    return {t for t in normalize(title).split() if t not in STOP and len(t) > 1}


def _normalize_brand(brand: str | None) -> str | None:
    if not brand:
        return None
    cleaned = _BRAND_NOISE.sub("", brand.strip()).lower().strip()
    return cleaned or None


@dataclass
class Attrs:
    brand: str | None = None
    model_codes: set[str] = field(default_factory=set)
    gtin: str | None = None
    storage: set[str] = field(default_factory=set)
    ram: set[str] = field(default_factory=set)
    size: set[str] = field(default_factory=set)
    colours: set[str] = field(default_factory=set)
    bundle: bool = False


def extract_attrs(title: str, brand: str | None = None, model: str | None = None,
                  gtin: str | None = None) -> Attrs:
    a = Attrs(
        brand=_normalize_brand(brand or ""),
        gtin=re.sub(r"\D", "", gtin or "") or None,
    )
    t = title or ""

    # RAM
    ram: set[str] = set()
    for m in _RAM.finditer(t):
        ram.add((m.group(1) or m.group(2)) + "gb")
    a.ram = ram

    # Storage (after masking RAM tokens)
    for m in _STORAGE.finditer(re.sub(_RAM, " ", t)):
        v = m.group(1) + m.group(2).lower()
        a.storage.add(v)

    # Heuristic: "8GB 256GB" without the word RAM — smaller is likely RAM
    if not a.ram and len(a.storage) >= 2:
        vals = sorted(
            a.storage,
            key=lambda s: int(re.sub(r"\D", "", s)) * (1024 if s.endswith("tb") else 1),
        )
        a.ram, a.storage = {vals[0]}, set(vals[1:])

    a.size   = {m.group(1) for m in _SIZE.finditer(t)}
    low      = normalize(t).split()
    a.colours = {w for w in low if w in COLOURS}
    a.bundle  = any(b in normalize(t) for b in BUNDLE_WORDS)

    codes = {c.lower() for c in _MODEL.findall(t.upper())}
    if model:
        codes.add(re.sub(r"\s+", "", model).lower())
    a.model_codes = {c for c in codes if not re.fullmatch(r"\d+(gb|tb|inch|cm|mm)", c)}

    if not a.brand and low:
        a.brand = _normalize_brand(low[0])

    return a


@dataclass
class MatchResult:
    status: str   # verified | possible | rejected
    score: Decimal
    reasons: list[str]


def compare(src: Attrs, src_title: str, cand: Attrs, cand_title: str) -> MatchResult:
    reasons: list[str] = []
    conflicts: list[str] = []

    # ── GTIN is definitive ────────────────────────────────────────────────
    if src.gtin and cand.gtin:
        if src.gtin.lstrip("0") == cand.gtin.lstrip("0"):
            return MatchResult("verified", Decimal("1.000"), ["Identical GTIN/EAN"])
        conflicts.append("Different GTIN")

    # ── Hard spec conflicts ───────────────────────────────────────────────
    for label, a, b in (
        ("storage",     src.storage, cand.storage),
        ("RAM",         src.ram,     cand.ram),
        ("screen size", src.size,    cand.size),
    ):
        if a and b and a != b:
            conflicts.append(f"Different {label}: {sorted(a)} vs {sorted(b)}")

    if src.bundle != cand.bundle:
        conflicts.append("Bundle/combo mismatch")

    # ── Brand conflict ────────────────────────────────────────────────────
    sb = _normalize_brand(src.brand)
    cb = _normalize_brand(cand.brand)
    if (sb and cb and sb != cb
            and sb not in normalize(cand_title)
            and cb not in normalize(src_title)):
        conflicts.append("Different brand")

    # ── Model-code overlap / conflict ─────────────────────────────────────
    model_overlap = src.model_codes & cand.model_codes
    # Partial containment handles hyphen/space variants: "s24ultra" vs "s24-ultra"
    partial_match = model_overlap or any(
        a in b or b in a
        for a in src.model_codes
        for b in cand.model_codes
    )
    model_conflict = bool(src.model_codes and cand.model_codes and not partial_match)
    if model_conflict:
        conflicts.append("Different model number")

    # ── Title similarity ──────────────────────────────────────────────────
    ts, tc = tokens(src_title), tokens(cand_title)
    jacc = len(ts & tc) / len(ts | tc) if ts | tc else 0.0
    seq  = SequenceMatcher(None, normalize(src_title), normalize(cand_title)).ratio()
    sim  = Decimal(str(round(0.5 * jacc + 0.5 * seq, 3)))

    colour_diff = bool(src.colours and cand.colours and src.colours != cand.colours)

    # ── Decision ──────────────────────────────────────────────────────────
    if conflicts:
        return MatchResult("rejected", min(sim, Decimal("0.4")), conflicts)

    if partial_match and sim >= Decimal("0.35"):
        best_code = sorted(model_overlap or cand.model_codes)[0].upper()
        reasons.append(f"Matching model number {best_code}")
        if sb and cb and sb == cb:
            reasons.append("Same brand")
        if src.storage and src.storage == cand.storage:
            reasons.append("Same storage")
        if colour_diff:
            return MatchResult(
                "possible", Decimal("0.800"),
                reasons + ["Colour variant — may be priced differently"],
            )
        return MatchResult("verified", max(Decimal("0.880"), sim), reasons)

    # No model code — rely on title similarity
    if sim >= Decimal("0.60"):
        return MatchResult(
            "possible", sim,
            ["Similar title — no shared model code or GTIN; verify manually"],
        )

    return MatchResult("rejected", sim, ["Titles too dissimilar"])