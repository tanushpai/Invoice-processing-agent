from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import models as m
from src.matching.text import norm_id, similarity
from src.schemas.invoice import NormalizedInvoice


class POCandidate(BaseModel):
    po_id: int
    po_number: str
    score: float
    reasons: list[str] = Field(default_factory=list)


class POMatch(BaseModel):
    status: Literal["explicit", "inferred", "ambiguous", "missing", "not_found", "vendor_conflict"]
    po_id: int | None = None
    po_number: str | None = None
    confidence: float = 0.0
    candidates: list[POCandidate] = Field(default_factory=list)
    note: str = ""

    @property
    def usable_for_recon(self) -> bool:
        return self.status in ("explicit", "inferred") and self.po_id is not None


@dataclass
class POItemFact:
    id: int
    line_no: int
    sku: str
    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_rate: Decimal


def map_lines_to_po_items(inv: NormalizedInvoice, po_items: list[POItemFact]) -> dict[int, int | None]:
    """invoice line_no -> po_item_id. SKU first, then strong description similarity. Never reuses a PO item."""
    mapping: dict[int, int | None] = {}
    used: set[int] = set()
    for li in inv.line_items:
        hit = None
        if li.sku:
            hit = next((p for p in po_items if p.id not in used and norm_id(p.sku) == norm_id(li.sku)), None)
        if hit is None and li.description:
            scored = [(similarity(li.description, p.description), p) for p in po_items if p.id not in used]
            scored.sort(key=lambda t: -t[0])
            if scored and scored[0][0] >= 0.85:
                hit = scored[0][1]
        mapping[li.line_no] = hit.id if hit else None
        if hit:
            used.add(hit.id)
    return mapping


def load_po_items(session: Session, po_id: int) -> list[POItemFact]:
    rows = session.scalars(select(m.PurchaseOrderItem).where(m.PurchaseOrderItem.po_id == po_id)
                           .order_by(m.PurchaseOrderItem.line_no)).all()
    return [POItemFact(r.id, r.line_no, r.sku, r.description, r.quantity, r.unit_price, r.tax_rate) for r in rows]


def prior_invoiced_by_item(session: Session, po_id: int, exclude_invoice_id: int | None = None) -> dict[int, Decimal]:
    """Quantity already billed per PO item by earlier, non-rejected invoices (split-invoice support)."""
    q = (select(m.InvoiceItem.po_item_id, func.sum(m.InvoiceItem.quantity))
         .join(m.Invoice, m.Invoice.id == m.InvoiceItem.invoice_id)
         .where(m.Invoice.po_id == po_id, m.Invoice.status != "REJECT", m.InvoiceItem.po_item_id.is_not(None))
         .group_by(m.InvoiceItem.po_item_id))
    if exclude_invoice_id:
        q = q.where(m.Invoice.id != exclude_invoice_id)
    return {pid: Decimal(qty or 0) for pid, qty in session.execute(q).all()}


def _score_candidate(session: Session, po: m.PurchaseOrder, inv: NormalizedInvoice) -> POCandidate:
    items = load_po_items(session, po.id)
    mapping = map_lines_to_po_items(inv, items)
    by_id = {i.id: i for i in items}
    prior = prior_invoiced_by_item(session, po.id)
    lines = inv.line_items or []
    reasons: list[str] = []
    if not lines:
        return POCandidate(po_id=po.id, po_number=po.po_number, score=0.0, reasons=["no invoice lines to compare"])
    matched = [(li, by_id[mapping[li.line_no]]) for li in lines if mapping.get(li.line_no)]
    overlap = len(matched) / len(lines)
    qty_fit = (sum(1 for li, p in matched if li.quantity is not None
                   and li.quantity <= p.quantity - prior.get(p.id, Decimal(0))) / len(matched)) if matched else 0
    price_fit = (sum(1 for li, p in matched if li.unit_price is not None and p.unit_price
                     and abs(li.unit_price - p.unit_price) / p.unit_price <= Decimal("0.02")) / len(matched)) if matched else 0
    date_fit = 1.0 if (inv.invoice_date is None or inv.invoice_date >= po.order_date) else 0.0
    score = 0.55 * overlap + 0.25 * qty_fit + 0.10 * price_fit + 0.10 * date_fit
    reasons.append(f"{len(matched)}/{len(lines)} invoice lines match PO items")
    if matched:
        reasons.append(f"quantities fit remaining PO balance for {round(qty_fit * 100)}% of matched lines")
        reasons.append(f"unit prices within 2% for {round(price_fit * 100)}% of matched lines")
    return POCandidate(po_id=po.id, po_number=po.po_number, score=round(score, 3), reasons=reasons)


def match_po(session: Session, inv: NormalizedInvoice, vendor_id: int | None) -> POMatch:
    raw = (inv.po_number or "").strip()
    if raw:
        po = session.scalar(select(m.PurchaseOrder).where(m.PurchaseOrder.po_number == raw.upper()))
        if po is None:
            po = next((p for p in session.scalars(select(m.PurchaseOrder)).all()
                       if norm_id(p.po_number) == norm_id(raw)), None)
        if po is not None:
            if vendor_id is not None and po.vendor_id != vendor_id:
                return POMatch(status="vendor_conflict", po_id=po.id, po_number=po.po_number, confidence=0.0,
                               note=f"PO {po.po_number} belongs to {po.vendor.name}, not the matched vendor.")
            return POMatch(status="explicit", po_id=po.id, po_number=po.po_number, confidence=1.0,
                           note="PO number printed on the invoice matches a purchase order.")
    if vendor_id is None:
        return POMatch(status="not_found" if raw else "missing",
                       note=(f"PO '{raw}' not found." if raw else "No PO on the invoice and vendor unknown."))

    pos = session.scalars(select(m.PurchaseOrder).where(m.PurchaseOrder.vendor_id == vendor_id,
                                                        m.PurchaseOrder.status == "open")).all()
    cands = sorted((_score_candidate(session, p, inv) for p in pos), key=lambda c: -c.score)
    cands = [c for c in cands if c.score >= 0.5]
    prefix = f"PO '{raw}' was not found. " if raw else "No PO reference on the invoice. "
    if not cands:
        return POMatch(status="not_found" if raw else "missing", note=prefix + "No open PO for this vendor fits the invoice lines.")
    if len(cands) == 1 or (cands[0].score >= 0.7 and cands[0].score - cands[1].score >= 0.25):
        top = cands[0]
        return POMatch(status="inferred", po_id=top.po_id, po_number=top.po_number, confidence=top.score,
                       candidates=cands[:3],
                       note=prefix + f"Best candidate is {top.po_number} (score {top.score:.2f}); it is a suggestion, not a confirmed match.")
    return POMatch(status="ambiguous", confidence=cands[0].score, candidates=cands[:3],
                   note=prefix + f"{len(cands)} POs are plausible - the system will not choose between them.")
