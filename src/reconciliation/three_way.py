"""Invoice vs PO vs Goods Receipt, split-invoice aware. Pure function: no DB, no AI."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import BaseModel, Field

from src.core.money import ZERO, pct_variance, q2
from src.matching.po import POItemFact
from src.schemas.invoice import NormalizedInvoice


@dataclass(frozen=True)
class Tolerances:
    amount_pct: Decimal = Decimal("2")
    qty_pct: Decimal = Decimal("0")
    receipt_pct: Decimal = Decimal("0")


class LineRecon(BaseModel):
    line_no: int
    sku: str | None = None
    description: str | None = None
    po_item_id: int | None = None
    invoice_qty: Decimal = ZERO
    po_qty: Decimal | None = None
    prior_invoiced_qty: Decimal = ZERO
    cumulative_invoiced_qty: Decimal = ZERO
    remaining_po_qty_after: Decimal | None = None
    received_qty: Decimal | None = None
    qty_over_po: Decimal = ZERO
    receipt_shortfall: Decimal = ZERO
    invoice_unit_price: Decimal | None = None
    po_unit_price: Decimal | None = None
    price_variance_pct: Decimal | None = None
    expected_line_total: Decimal | None = None
    flags: list[str] = Field(default_factory=list)


class ReconResult(BaseModel):
    lines: list[LineRecon] = Field(default_factory=list)
    expected_subtotal: Decimal | None = None
    expected_tax: Decimal | None = None
    expected_total: Decimal | None = None
    invoice_total: Decimal | None = None
    total_variance: Decimal | None = None
    total_variance_pct: Decimal | None = None
    unmatched_line_nos: list[int] = Field(default_factory=list)
    receipt_count: int = 0
    against_inferred_po: bool = False
    tolerances: dict[str, str] = Field(default_factory=dict)

    @property
    def max_abs_price_variance(self) -> Decimal:
        vals = [abs(line.price_variance_pct) for line in self.lines if line.price_variance_pct is not None]
        return max(vals, default=ZERO)


def reconcile(inv: NormalizedInvoice, po_items: list[POItemFact], mapping: dict[int, int | None],
              receipts_by_item: dict[int, Decimal], prior_by_item: dict[int, Decimal], tol: Tolerances,
              receipt_count: int, against_inferred_po: bool = False) -> ReconResult:
    by_id = {p.id: p for p in po_items}
    res = ReconResult(receipt_count=receipt_count, against_inferred_po=against_inferred_po,
                      invoice_total=inv.total,
                      tolerances={"amount_pct": str(tol.amount_pct), "qty_pct": str(tol.qty_pct),
                                  "receipt_pct": str(tol.receipt_pct)})
    exp_sub, exp_tax, all_mapped = ZERO, ZERO, True

    for li in inv.line_items:
        qty = li.quantity if li.quantity is not None else ZERO
        lr = LineRecon(line_no=li.line_no, sku=li.sku, description=li.description, invoice_qty=qty,
                       invoice_unit_price=li.unit_price)
        if li.quantity is None:
            lr.flags.append("NO_QTY")
            all_mapped = False
        pid = mapping.get(li.line_no)
        if pid is None:
            lr.flags.append("NOT_ON_PO")
            res.unmatched_line_nos.append(li.line_no)
            all_mapped = False
            res.lines.append(lr)
            continue
        p = by_id[pid]
        prior = prior_by_item.get(pid, ZERO)
        cum = prior + qty
        recv = receipts_by_item.get(pid, ZERO)
        lr.po_item_id, lr.po_qty, lr.po_unit_price = pid, p.quantity, p.unit_price
        lr.prior_invoiced_qty, lr.cumulative_invoiced_qty, lr.received_qty = prior, cum, recv
        lr.remaining_po_qty_after = p.quantity - cum
        lr.qty_over_po = max(cum - p.quantity, ZERO)
        lr.receipt_shortfall = max(cum - recv, ZERO)
        if lr.qty_over_po > p.quantity * tol.qty_pct / 100:
            lr.flags.append("OVER_PO_QTY")
        if lr.receipt_shortfall > cum * tol.receipt_pct / 100:
            lr.flags.append("RECEIPT_SHORTFALL")
        if li.unit_price is not None:
            lr.price_variance_pct = pct_variance(li.unit_price, p.unit_price)
            if lr.price_variance_pct is not None and abs(lr.price_variance_pct) > tol.amount_pct:
                lr.flags.append("PRICE_VARIANCE")
        lr.expected_line_total = q2(qty * p.unit_price)
        exp_sub += lr.expected_line_total
        exp_tax += q2(lr.expected_line_total * p.tax_rate / 100)
        res.lines.append(lr)

    if all_mapped and inv.line_items:
        res.expected_subtotal, res.expected_tax = q2(exp_sub), q2(exp_tax)
        res.expected_total = q2(exp_sub + exp_tax)
        if inv.total is not None:
            res.total_variance = q2(inv.total - res.expected_total)
            res.total_variance_pct = pct_variance(inv.total, res.expected_total)
    return res
