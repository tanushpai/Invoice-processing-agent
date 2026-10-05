"""Decimal-only money helpers. Floats never touch a financial number in this codebase."""
from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
ZERO = Decimal("0")
_NUM = re.compile(r"^-?\d+(\.\d+)?$")
_JUNK = re.compile(r"(?i)(₹|rs\.?|inr|usd|eur|\$|€|/-|\s)")


def q2(x: Decimal | int | str) -> Decimal:
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)


def parse_decimal(value: object) -> Decimal | None:
    """Parse '₹1,23,456.50', '(120.00)', '18%' -> Decimal. Returns None if not a clean number."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    s = str(value).strip()
    if not s:
        return None
    neg = s.startswith("(") and s.endswith(")")
    s = _JUNK.sub("", s).replace(",", "").replace("%", "").strip("()")
    if neg:
        s = "-" + s
    if not _NUM.match(s):
        return None
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def pct_variance(actual: Decimal, expected: Decimal) -> Decimal | None:
    """Signed % variance of actual vs expected; None when expected is zero."""
    if expected == 0:
        return None
    return ((actual - expected) / expected * 100).quantize(Decimal("0.01"), ROUND_HALF_UP)


def fmt_inr(x: Decimal | float | int | None, symbol: str = "₹") -> str:
    """Indian digit grouping: 1234567.5 -> ₹12,34,567.50"""
    if x is None:
        return "—"
    d = q2(Decimal(str(x)))
    sign = "-" if d < 0 else ""
    whole, frac = f"{abs(d):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return f"{sign}{symbol}{whole}.{frac}"


def fmt_qty(x: Decimal | None) -> str:
    if x is None:
        return "—"
    return format(x.normalize(), "f")


def mask_account(acct: str | None) -> str:
    if not acct:
        return "—"
    digits = re.sub(r"\W", "", acct)
    return "•" * max(len(digits) - 4, 0) + digits[-4:]
