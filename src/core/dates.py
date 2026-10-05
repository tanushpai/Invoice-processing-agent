"""Date parsing with explicit ambiguity detection (03/04/2026 is NOT guessed)."""
from __future__ import annotations

import re
from datetime import date, datetime

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMERIC = re.compile(r"^\s*(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})\s*$")
_FORMATS = ["%Y-%m-%d", "%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y",
            "%d-%b-%y", "%d %b, %Y", "%d/%b/%Y"]


def is_ambiguous_numeric_date(raw: str | None) -> bool:
    if not raw:
        return False
    m = _NUMERIC.match(raw)
    if not m:
        return False
    a, b = int(m.group(1)), int(m.group(2))
    return a <= 12 and b <= 12 and a != b


def parse_date(raw: str | None) -> tuple[date | None, bool]:
    """Returns (date, ambiguous). Ambiguous numeric dates return (None, True)."""
    if not raw or not str(raw).strip():
        return None, False
    s = str(raw).strip()
    if _ISO.match(s):
        try:
            return datetime.strptime(s, "%Y-%m-%d").date(), False
        except ValueError:
            return None, False
    m = _NUMERIC.match(s)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if y < 100:
            y += 2000
        if a <= 12 and b <= 12 and a != b:
            return None, True
        try:  # day > 12 forces DD/MM; a == b is identical either way
            return date(y, b, a), False
        except ValueError:
            try:
                return date(y, a, b), False
            except ValueError:
                return None, False
    for fmt in _FORMATS:
        try:
            return datetime.strptime(s, fmt).date(), False
        except ValueError:
            continue
    return None, False
