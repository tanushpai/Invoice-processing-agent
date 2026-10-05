from __future__ import annotations

import re
from difflib import SequenceMatcher

_SUFFIXES = {"pvt", "private", "ltd", "limited", "llp", "inc", "co", "company", "corp", "corporation", "the", "and"}


def normalize_name(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(t for t in s.split() if t not in _SUFFIXES)


def similarity(a: str | None, b: str | None) -> float:
    a, b = normalize_name(a), normalize_name(b)
    if not a or not b:
        return 0.0
    seq = SequenceMatcher(None, a, b).ratio()
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0.0
    return max(seq, jac)


def norm_id(s: str | None) -> str:
    """Alphanumeric-only uppercase key: 'ABC/INV/1024' == 'abc-inv-1024'."""
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")
