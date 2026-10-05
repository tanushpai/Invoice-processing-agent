"""Does the invoice agree with ITSELF? (line maths, tax, subtotal, total). Pure Decimal arithmetic."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, Field

from src.core.money import fmt_inr, q2
from src.schemas.invoice import NormalizedInvoice


class ArithmeticIssue(BaseModel):
    code: str
    message: str
    expected: Decimal | None = None
    actual: Decimal | None = None


class ArithmeticResult(BaseModel):
    issues: list[ArithmeticIssue] = Field(default_factory=list)
    checks_run: int = 0
    lines_sum: Decimal | None = None
    tax_sum: Decimal | None = None
    expected_total: Decimal | None = None


def check_invoice_arithmetic(inv: NormalizedInvoice, tol: Decimal = Decimal("1.00")) -> ArithmeticResult:
    res = ArithmeticResult()

    def differs(a: Decimal, b: Decimal) -> bool:
        return abs(a - b) > tol

    for li in inv.line_items:
        if li.quantity is not None and li.unit_price is not None and li.line_total is not None:
            res.checks_run += 1
            exp = q2(li.quantity * li.unit_price)
            if differs(exp, li.line_total):
                res.issues.append(ArithmeticIssue(
                    code="LINE_TOTAL", expected=exp, actual=li.line_total,
                    message=f"Line {li.line_no}: {li.quantity} x {fmt_inr(li.unit_price)} = {fmt_inr(exp)}, "
                            f"but invoice shows {fmt_inr(li.line_total)}."))
        if li.line_total is not None and li.tax_rate is not None and li.tax_amount is not None:
            res.checks_run += 1
            exp = q2(li.line_total * li.tax_rate / 100)
            if differs(exp, li.tax_amount):
                res.issues.append(ArithmeticIssue(
                    code="LINE_TAX", expected=exp, actual=li.tax_amount,
                    message=f"Line {li.line_no}: tax at {li.tax_rate}% should be {fmt_inr(exp)}, shows {fmt_inr(li.tax_amount)}."))

    if inv.line_items and all(li.line_total is not None for li in inv.line_items):
        res.lines_sum = q2(sum((li.line_total for li in inv.line_items), Decimal(0)))
        if inv.subtotal is not None:
            res.checks_run += 1
            if differs(res.lines_sum, inv.subtotal):
                res.issues.append(ArithmeticIssue(
                    code="SUBTOTAL", expected=res.lines_sum, actual=inv.subtotal,
                    message=f"Line items add up to {fmt_inr(res.lines_sum)} but subtotal is {fmt_inr(inv.subtotal)}."))

    if inv.line_items:
        if all(li.tax_amount is not None for li in inv.line_items):
            res.tax_sum = q2(sum((li.tax_amount for li in inv.line_items), Decimal(0)))
        elif all(li.line_total is not None and li.tax_rate is not None for li in inv.line_items):
            res.tax_sum = q2(sum((li.line_total * li.tax_rate / 100 for li in inv.line_items), Decimal(0)))
    if res.tax_sum is not None and inv.tax is not None:
        res.checks_run += 1
        if differs(res.tax_sum, inv.tax):
            res.issues.append(ArithmeticIssue(
                code="TAX_TOTAL", expected=res.tax_sum, actual=inv.tax,
                message=f"Line taxes add up to {fmt_inr(res.tax_sum)} but invoice tax is {fmt_inr(inv.tax)}."))

    if inv.subtotal is not None and inv.tax is not None and inv.total is not None:
        res.checks_run += 1
        res.expected_total = q2(inv.subtotal + inv.tax)
        if differs(res.expected_total, inv.total):
            res.issues.append(ArithmeticIssue(
                code="GRAND_TOTAL", expected=res.expected_total, actual=inv.total,
                message=f"Subtotal {fmt_inr(inv.subtotal)} + tax {fmt_inr(inv.tax)} = {fmt_inr(res.expected_total)}, "
                        f"but the invoice total is {fmt_inr(inv.total)} (difference {fmt_inr(inv.total - res.expected_total)})."))
    return res
