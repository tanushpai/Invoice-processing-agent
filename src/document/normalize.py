"""Turn AI output (strings) into typed, validated invoice data. Python owns the numbers."""
from __future__ import annotations

import hashlib
import re

from src.core.dates import is_ambiguous_numeric_date, parse_date
from src.core.money import parse_decimal, q2
from src.schemas.invoice import (
    DATE_FIELDS,
    HEADER_FIELDS,
    MONEY_FIELDS,
    ExtractedField,
    FieldMeta,
    NormalizedInvoice,
    NormalizedLineItem,
    RawInvoiceExtraction,
)

_CURRENCY_MAP = {"₹": "INR", "RS": "INR", "RS.": "INR", "INR": "INR", "RUPEES": "INR", "$": "USD", "USD": "USD",
                 "€": "EUR", "EUR": "EUR"}


def content_hash(text: str) -> str:
    """Hash of normalised content so the same invoice re-sent under another filename still collides."""
    norm = re.sub(r"[^a-z0-9]", "", text.lower())
    return hashlib.sha256(norm.encode()).hexdigest()


def _clean_str(v: str | None) -> str | None:
    if v is None:
        return None
    v = re.sub(r"\s+", " ", v).strip()
    return v or None


def normalize_extraction(raw: RawInvoiceExtraction) -> NormalizedInvoice:
    out: dict = {}
    meta: dict[str, FieldMeta] = {}
    warnings = list(raw.document_warnings)

    for name in HEADER_FIELDS:
        f = raw.fields.get(name)
        if f is None:
            f = ExtractedField(value=None, confidence=0.0, warning="Field was not returned by the extractor")
        fm = FieldMeta(confidence=f.confidence, source_label=f.source_label, raw_value=f.raw_value,
                       warning=f.warning, human_review_required=f.human_review_required)
        value = _clean_str(f.value)

        if name in DATE_FIELDS:
            printed = f.raw_value or f.value
            if is_ambiguous_numeric_date(printed):
                value_parsed, ambiguous = None, True
            else:
                value_parsed, ambiguous = parse_date(value)
            if ambiguous:
                fm.warning = (f"Date '{printed}' is ambiguous (DD/MM vs MM/DD) - not guessed")
                fm.human_review_required = True
                fm.confidence = min(fm.confidence or 1.0, 0.42)
            elif value and value_parsed is None:
                fm.warning = f"Could not parse date '{value}'"
                fm.human_review_required = True
                fm.confidence = min(fm.confidence, 0.3)
            out[name] = value_parsed
        elif name in MONEY_FIELDS:
            d = parse_decimal(value) if value else None
            if value and d is None:
                fm.warning = f"'{value}' is not a valid amount"
                fm.human_review_required = True
                fm.confidence = min(fm.confidence, 0.3)
            out[name] = q2(d) if d is not None else None
        elif name == "currency":
            out[name] = _CURRENCY_MAP.get(value.upper(), value.upper()) if value else None
        elif name == "vendor_tax_id":
            out[name] = value.upper().replace(" ", "") if value else None
        else:
            out[name] = value

        if out[name] is None and not fm.warning and not fm.human_review_required:
            fm.warning = fm.warning or None
        meta[name] = fm

    items = []
    for i, li in enumerate(raw.line_items, start=1):
        qty = parse_decimal(li.quantity)
        price = parse_decimal(li.unit_price)
        rate = parse_decimal(li.tax_rate)
        tax_amt = parse_decimal(li.tax_amount)
        total = parse_decimal(li.line_total)
        items.append(NormalizedLineItem(
            line_no=i, description=_clean_str(li.description), sku=_clean_str(li.sku),
            quantity=qty, unit_price=q2(price) if price is not None else None, tax_rate=rate,
            tax_amount=q2(tax_amt) if tax_amt is not None else None,
            line_total=q2(total) if total is not None else None,
            confidence=li.confidence, warning=li.warning))
    return NormalizedInvoice(**out, line_items=items, meta=meta, warnings=warnings)
