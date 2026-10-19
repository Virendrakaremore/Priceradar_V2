"""Money parsing. Never returns 0 for 'could not parse'."""
from __future__ import annotations
import re
from decimal import Decimal, InvalidOperation

_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")

def parse_price(raw) -> Decimal | None:
    if raw is None:
        return None
    if isinstance(raw, (int, float, Decimal)):
        try:
            d = Decimal(str(raw))
        except InvalidOperation:
            return None
    else:
        m = _NUM.search(str(raw))
        if not m:
            return None
        try:
            d = Decimal(m.group(0).replace(",", ""))
        except InvalidOperation:
            return None
    if d <= 0 or d > Decimal("100000000"):
        return None
    return d.quantize(Decimal("0.01"))
