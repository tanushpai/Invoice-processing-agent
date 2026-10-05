from decimal import Decimal as D

from src.matching.po import POItemFact
from src.reconciliation.arithmetic import check_invoice_arithmetic
from src.reconciliation.three_way import Tolerances, reconcile
from src.schemas.invoice import NormalizedInvoice, NormalizedLineItem

PO = [POItemFact(1, 1, "SKU-1", "Widget", D(100), D("100.00"), D(18))]


def inv(qty, price="100.00"):
    q, p = D(qty), D(price)
    lt = q * p
    return NormalizedInvoice(line_items=[NormalizedLineItem(line_no=1, sku="SKU-1", quantity=q, unit_price=p, tax_rate=D(18), line_total=lt, tax_amount=lt * D("0.18"))],
                             subtotal=lt, tax=lt * D("0.18"), total=lt * D("1.18"))


def recon(i, received, prior=0, **kw):
    return reconcile(i, PO, {1: 1}, {1: D(received)}, {1: D(prior)}, Tolerances(**kw), receipt_count=1)


def test_partial_delivery_flags_shortfall_of_20():
    r = recon(inv(100), received=80)
    ln = r.lines[0]
    assert ln.receipt_shortfall == D(20) and "RECEIPT_SHORTFALL" in ln.flags and "OVER_PO_QTY" not in ln.flags


def test_split_invoice_cumulative_is_ok():
    # PO 100; previous 40 + 30; current 30; received 100
    r = recon(inv(30), received=100, prior=70)
    ln = r.lines[0]
    assert ln.cumulative_invoiced_qty == D(100) and ln.flags == [] and ln.remaining_po_qty_after == 0


def test_split_invoice_over_billing_detected():
    r = recon(inv(40), received=100, prior=70)
    assert "OVER_PO_QTY" in r.lines[0].flags and r.lines[0].qty_over_po == D(10)


def test_price_within_and_beyond_tolerance():
    assert "PRICE_VARIANCE" not in recon(inv(10, "101.50"), received=100).lines[0].flags      # 1.5% < 2%
    assert "PRICE_VARIANCE" in recon(inv(10, "103.00"), received=100).lines[0].flags          # 3% > 2%


def test_unmapped_line_is_flagged_not_guessed():
    r = reconcile(inv(10), PO, {1: None}, {}, {}, Tolerances(), 0)
    assert r.unmatched_line_nos == [1] and r.expected_total is None


def test_arithmetic_passes_and_catches_wrong_total():
    ok = inv(10)
    assert check_invoice_arithmetic(ok).issues == []
    bad = inv(10)
    bad.total += D(1000)
    codes = [i.code for i in check_invoice_arithmetic(bad).issues]
    assert codes == ["GRAND_TOTAL"]


def test_arithmetic_catches_line_and_subtotal_errors():
    bad = inv(10)
    bad.line_items[0].line_total = D("900.00")
    codes = {i.code for i in check_invoice_arithmetic(bad).issues}
    assert {"LINE_TOTAL", "SUBTOTAL"} <= codes
