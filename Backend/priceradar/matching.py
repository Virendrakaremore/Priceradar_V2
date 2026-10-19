"""Conservative product matching. A 'verified' match needs hard evidence; similar words are not enough."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal
from difflib import SequenceMatcher

COLOURS = {"black","white","blue","red","green","silver","gold","grey","gray","pink","purple","yellow","orange",
           "titanium","graphite","midnight","starlight","violet","lavender","cream","beige","brown","navy","teal",
           "natural","desert"}
BUNDLE_WORDS = {"combo","bundle","pack of","set of","with free","kit"}
STOP = {"the","and","with","for","new","latest","buy","online","india","in","by","of","a","smartphone","mobile","phone","5g","4g"}
_STORAGE = re.compile(r"(\d+)\s?(gb|tb)\b", re.I)
_RAM = re.compile(r"(\d+)\s?gb\s*(?:ram|memory)\b|\bram\s*(\d+)\s?gb", re.I)
_SIZE = re.compile(r"(\d{2,3}(?:\.\d)?)\s?(?:inch|inches|\"|”|cm)", re.I)
_MODEL = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z0-9][A-Z0-9-]{3,}\b")


def normalize(title: str) -> str:
    t = re.sub(r"[^a-z0-9. ]+", " ", (title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def tokens(title: str) -> set[str]:
    return {t for t in normalize(title).split() if t not in STOP and len(t) > 1}


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


def extract_attrs(title: str, brand: str | None = None, model: str | None = None, gtin: str | None = None) -> Attrs:
    a = Attrs(brand=(brand or "").lower() or None, gtin=re.sub(r"\D", "", gtin or "") or None)
    t = title or ""
    ram = set()
    for m in _RAM.finditer(t):
        ram.add((m.group(1) or m.group(2)) + "gb")
    a.ram = ram
    for m in _STORAGE.finditer(re.sub(_RAM, " ", t)):
        v = m.group(1) + m.group(2).lower()
        a.storage.add(v)
    # a "8GB 256GB" pair without the word RAM: smaller figure is RAM
    if not a.ram and len(a.storage) >= 2:
        vals = sorted(a.storage, key=lambda s: int(re.sub(r"\D", "", s)) * (1024 if s.endswith("tb") else 1))
        a.ram, a.storage = {vals[0]}, set(vals[1:])
    a.size = {m.group(1) for m in _SIZE.finditer(t)}
    low = normalize(t).split()
    a.colours = {w for w in low if w in COLOURS}
    a.bundle = any(b in normalize(t) for b in BUNDLE_WORDS)
    codes = {c.lower() for c in _MODEL.findall(t.upper())}
    if model:
        codes.add(re.sub(r"\s+", "", model).lower())
    # drop tokens that are really capacities (e.g. 256GB) or sizes
    a.model_codes = {c for c in codes if not re.fullmatch(r"\d+(gb|tb)", c)}
    if not a.brand and low:
        a.brand = low[0]
    return a


@dataclass
class MatchResult:
    status: str  # verified | possible | rejected
    score: Decimal
    reasons: list[str]


def compare(src: Attrs, src_title: str, cand: Attrs, cand_title: str) -> MatchResult:
    reasons: list[str] = []
    conflicts: list[str] = []
    if src.gtin and cand.gtin:
        if src.gtin.lstrip("0") == cand.gtin.lstrip("0"):
            return MatchResult("verified", Decimal("1.000"), ["Identical GTIN/EAN"])
        conflicts.append("Different GTIN")
    for label, a, b in (("storage", src.storage, cand.storage), ("RAM", src.ram, cand.ram), ("screen size", src.size, cand.size)):
        if a and b and a != b:
            conflicts.append(f"Different {label}: {sorted(a)} vs {sorted(b)}")
    if src.bundle != cand.bundle:
        conflicts.append("Bundle/combo mismatch")
    if src.brand and cand.brand and src.brand != cand.brand and src.brand not in normalize(cand_title) \
            and cand.brand not in normalize(src_title):
        conflicts.append("Different brand")
    model_overlap = src.model_codes & cand.model_codes
    model_conflict = bool(src.model_codes and cand.model_codes and not model_overlap
                          and not any(a in b or b in a for a in src.model_codes for b in cand.model_codes))
    if model_conflict:
        conflicts.append("Different model number")
    ts, tc = tokens(src_title), tokens(cand_title)
    jacc = len(ts & tc) / len(ts | tc) if ts | tc else 0.0
    seq = SequenceMatcher(None, normalize(src_title), normalize(cand_title)).ratio()
    sim = Decimal(str(round(0.5 * jacc + 0.5 * seq, 3)))
    colour_diff = bool(src.colours and cand.colours and src.colours != cand.colours)

    if conflicts:
        return MatchResult("rejected", min(sim, Decimal("0.4")), conflicts)
    if model_overlap and sim >= Decimal("0.45"):
        reasons.append(f"Matching model number {sorted(model_overlap)[0].upper()}")
        if src.brand and cand.brand and src.brand == cand.brand:
            reasons.append("Same brand")
        if src.storage and src.storage == cand.storage:
            reasons.append("Same storage")
        if colour_diff:
            return MatchResult("possible", Decimal("0.800"), reasons + ["Colour differs — price may differ"])
        return MatchResult("verified", max(Decimal("0.900"), sim), reasons)
    if sim >= Decimal("0.70"):
        return MatchResult("possible", sim, ["Similar title but no shared model number or GTIN to verify identity"])
    return MatchResult("rejected", sim, ["Titles too dissimilar"])
