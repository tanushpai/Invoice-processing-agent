from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from src.database import models as m
from src.matching.text import norm_id
from src.schemas.invoice import NormalizedInvoice


class DuplicateMatch(BaseModel):
    invoice_id: int
    invoice_number: str | None
    vendor_name: str | None
    total: str | None
    score: float
    signals: list[str]


class DuplicateResult(BaseModel):
    is_duplicate: bool = False
    is_suspect: bool = False
    top_score: float = 0.0
    matches: list[DuplicateMatch] = Field(default_factory=list)


def _fingerprint(items) -> list[tuple[str, str]]:
    return sorted((norm_id(i.sku or i.description or ""), str(i.quantity.normalize() if i.quantity is not None else ""))
                  for i in items)


def detect_duplicates(session: Session, inv: NormalizedInvoice, vendor_id: int | None, po_id: int | None,
                      file_hash: str | None, content_hash: str | None, reject_threshold: float,
                      suspect_threshold: float, exclude_invoice_id: int | None = None) -> DuplicateResult:
    conds = []
    if vendor_id:
        conds.append(m.Invoice.vendor_id == vendor_id)
    if inv.total is not None:
        conds.append(m.Invoice.total == inv.total)
    if file_hash:
        conds.append(m.Invoice.file_hash == file_hash)
    if content_hash:
        conds.append(m.Invoice.content_hash == content_hash)
    if inv.invoice_number:
        conds.append(m.Invoice.invoice_number.is_not(None))
    if not conds:
        return DuplicateResult()
    q = select(m.Invoice).where(or_(*conds))
    if exclude_invoice_id:
        q = q.where(m.Invoice.id != exclude_invoice_id)
    mine = _fingerprint(inv.line_items)
    out: list[DuplicateMatch] = []
    for other in session.scalars(q).all():
        sig: list[str] = []
        if (file_hash and other.file_hash == file_hash) or (content_hash and other.content_hash == content_hash):
            out.append(DuplicateMatch(invoice_id=other.id, invoice_number=other.invoice_number,
                                      vendor_name=other.vendor.name if other.vendor else other.vendor_name_raw,
                                      total=str(other.total), score=1.0,
                                      signals=["identical document content (file or normalised text hash)"]))
            continue
        score = 0.0
        if vendor_id and other.vendor_id == vendor_id:
            score += 0.20
            sig.append("same vendor")
        a, b = norm_id(inv.invoice_number), norm_id(other.invoice_number)
        if a and b:
            if a == b:
                score += 0.35
                sig.append("same invoice number (after normalising punctuation/case)")
            elif a in b or b in a:
                score += 0.15
                sig.append("similar invoice number")
        if po_id and other.po_id == po_id:
            score += 0.10
            sig.append("same PO")
        if inv.total is not None and other.total is not None and abs(inv.total - other.total) < 1:
            score += 0.20
            sig.append("same total amount")
        if inv.invoice_date and other.invoice_date:
            if inv.invoice_date == other.invoice_date:
                score += 0.10
                sig.append("same invoice date")
            elif abs(inv.invoice_date - other.invoice_date) <= timedelta(days=5):
                score += 0.05
                sig.append("invoice date within 5 days")
        if mine and mine == _fingerprint(other.items):
            score += 0.05
            sig.append("identical line items")
        if score >= 0.3 and sig:
            out.append(DuplicateMatch(invoice_id=other.id, invoice_number=other.invoice_number,
                                      vendor_name=other.vendor.name if other.vendor else other.vendor_name_raw,
                                      total=str(other.total), score=round(min(score, 1.0), 3), signals=sig))
    out.sort(key=lambda d: -d.score)
    top = out[0].score if out else 0.0
    return DuplicateResult(is_duplicate=top >= reject_threshold,
                           is_suspect=suspect_threshold <= top < reject_threshold,
                           top_score=top, matches=out[:3])
