"""Deterministic, configurable, versioned business rules. No AI in this file."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.core.money import ZERO, fmt_inr, mask_account
from src.database import models as m
from src.matching.duplicates import DuplicateResult
from src.matching.po import POMatch
from src.matching.vendor import BankCheck, VendorMatch
from src.reconciliation.arithmetic import ArithmeticResult
from src.reconciliation.three_way import ReconResult
from src.schemas.decision import Decision, PolicyResult, most_restrictive
from src.schemas.invoice import CRITICAL_FIELDS, NormalizedInvoice

DEFAULT_POLICY_NAME = "AP Default Policy"
DEFAULT_RULES: dict = {
    "amount_tolerance_pct": 2.0,
    "quantity_tolerance_pct": 0.0,
    "receipt_tolerance_pct": 0.0,
    "duplicate_reject_threshold": 0.90,
    "duplicate_suspect_threshold": 0.60,
    "min_field_confidence": 0.70,
    "arithmetic_tolerance_inr": 1.00,
    "high_value_threshold_inr": 500000,
    "unusual_amount_multiple": 3.0,
    "unusual_amount_min_history": 3,
    "bank_mismatch_action": "HUMAN_REVIEW",
    "missing_po_action": "HUMAN_REVIEW",
    "missing_receipt_action": "HOLD",
}
RULE_DOCS: dict[str, str] = {
    "amount_tolerance_pct": "Max price / total variance vs PO (%) before the invoice is held.",
    "quantity_tolerance_pct": "Max cumulative over-billing vs PO quantity (%).",
    "receipt_tolerance_pct": "Max billed-but-not-received quantity (% of billed). 0 = bill only what was received.",
    "duplicate_reject_threshold": "Duplicate similarity score at/above which the invoice is rejected.",
    "duplicate_suspect_threshold": "Similarity score at/above which a human must check for a duplicate.",
    "min_field_confidence": "Extracted fields below this confidence need human confirmation.",
    "arithmetic_tolerance_inr": "Rounding tolerance (absolute, INR) for the invoice's own arithmetic.",
    "high_value_threshold_inr": "Invoices above this total always need an authorised approver.",
    "unusual_amount_multiple": "Flag invoices larger than N x the vendor's average invoice.",
    "unusual_amount_min_history": "Minimum prior invoices before the unusual-amount rule applies.",
    "bank_mismatch_action": "Decision when the bank account is not registered for the vendor.",
    "missing_po_action": "Decision when no single PO can be confirmed.",
    "missing_receipt_action": "Decision when billed goods have not been received.",
}


class PolicyFacts(BaseModel):
    invoice: NormalizedInvoice
    vendor: VendorMatch
    bank: BankCheck
    po: POMatch
    po_currency: str | None = None
    recon: ReconResult | None = None
    recon_skipped_reason: str | None = None
    arithmetic: ArithmeticResult
    duplicate: DuplicateResult
    vendor_avg_total: Decimal | None = None
    vendor_invoice_count: int = 0


def _d(rules: dict, key: str) -> Decimal:
    return Decimal(str(rules.get(key, DEFAULT_RULES[key])))


def _act(rules: dict, key: str) -> Decimal | Decision:
    return Decision(rules.get(key, DEFAULT_RULES[key]))


def _skip(rule_id: str, name: str, why: str) -> PolicyResult:
    return PolicyResult(rule_id=rule_id, name=name, status="skipped", detail=why)


def evaluate(f: PolicyFacts, rules: dict) -> list[PolicyResult]:
    inv, out = f.invoice, []
    min_conf = float(rules.get("min_field_confidence", 0.7))

    # 1. critical data present
    missing = [x for x in CRITICAL_FIELDS if getattr(inv, x) is None
               and not (inv.meta.get(x) and inv.meta[x].human_review_required)]
    if not inv.line_items:
        missing.append("line_items")
    out.append(PolicyResult(
        rule_id="critical_data", name="Critical data present", threshold="vendor, invoice no., date, total, lines",
        status="fail" if missing else "pass", impact=Decision.CORRECTION_REQUIRED if missing else None,
        detail=f"Missing: {', '.join(missing)}." if missing else "All critical fields were found.",
        evidence=[("Missing fields", ", ".join(missing))] if missing else [],
        refs=[f"extraction:{x}" for x in missing]))

    # 2. ambiguity / low confidence
    flagged = inv.review_fields(min_conf)
    ev = [(x, f"conf {inv.meta[x].confidence:.2f}" + (f" - {inv.meta[x].warning}" if inv.meta[x].warning else ""))
          for x in flagged]
    out.append(PolicyResult(
        rule_id="ambiguous_extraction", name="Extraction certainty", threshold=f"confidence >= {min_conf:.2f}",
        status="fail" if flagged else "pass", impact=Decision.HUMAN_REVIEW if flagged else None,
        detail=(f"{len(flagged)} field(s) ambiguous or low-confidence: {', '.join(flagged)}." if flagged
                else "All extracted fields are unambiguous and above the confidence threshold."),
        evidence=ev, refs=[f"extraction:{x}" for x in flagged]))

    # 3. vendor
    v = f.vendor
    out.append(PolicyResult(
        rule_id="vendor_known", name="Approved vendor", threshold="matched in vendor master",
        status="pass" if v.status == "matched" else "fail",
        impact=None if v.status == "matched" else Decision.HUMAN_REVIEW,
        detail=(f"Matched to {v.vendor_name} ({v.method}, {v.confidence:.0%})." if v.status == "matched"
                else (v.notes[0] if v.notes else "Vendor could not be confirmed.")),
        evidence=[("Printed vendor", inv.vendor_name or "—"), ("Match status", v.status)], refs=["vendor"]))

    # 4. bank
    b, bank_action = f.bank, _act(rules, "bank_mismatch_action")
    if b.status == "mismatch":
        out.append(PolicyResult(
            rule_id="bank_account", name="Bank account verification", threshold="must equal a registered account",
            status="fail", impact=bank_action, detail=b.note,
            evidence=[("Invoice account", b.invoice_account_masked or "—"),
                      ("Registered accounts", ", ".join(b.registered_masked) or "—")]
                     + ([("Registered to another vendor", b.belongs_to_other_vendor)] if b.belongs_to_other_vendor else []),
            refs=["bank"]))
    else:
        st = "pass" if b.status == "match" else "warn"
        out.append(PolicyResult(rule_id="bank_account", name="Bank account verification",
                                threshold="must equal a registered account", status=st,
                                detail="Bank account matches a registered account." if b.status == "match" else b.note,
                                refs=["bank"]))

    # 5. duplicates
    d, rej, sus = f.duplicate, float(rules.get("duplicate_reject_threshold", .9)), float(rules.get("duplicate_suspect_threshold", .6))
    if d.matches and d.top_score >= sus:
        top = d.matches[0]
        out.append(PolicyResult(
            rule_id="duplicate", name="Duplicate invoice check", threshold=f"reject >= {rej:.0%}, review >= {sus:.0%}",
            status="fail", impact=Decision.REJECT if d.top_score >= rej else Decision.HUMAN_REVIEW,
            detail=(f"{d.top_score:.0%} similar to {top.invoice_number or 'invoice #' + str(top.invoice_id)}: "
                    + "; ".join(top.signals) + "."),
            evidence=[("Closest existing invoice", f"{top.invoice_number} (id {top.invoice_id})"),
                      ("Similarity", f"{d.top_score:.0%}")], refs=["dup"]))
    else:
        out.append(PolicyResult(rule_id="duplicate", name="Duplicate invoice check",
                                threshold=f"reject >= {rej:.0%}, review >= {sus:.0%}", status="pass",
                                detail=f"Highest similarity to any existing invoice: {d.top_score:.0%}.", refs=["dup"]))

    # 6. PO reference
    po, po_action = f.po, _act(rules, "missing_po_action")
    if po.status == "explicit":
        out.append(PolicyResult(rule_id="po_reference", name="PO reference", threshold="exactly one confirmed PO",
                                status="pass", detail=f"PO {po.po_number} confirmed.", refs=["po"]))
    else:
        out.append(PolicyResult(rule_id="po_reference", name="PO reference", threshold="exactly one confirmed PO",
                                status="fail", impact=po_action, detail=po.note,
                                evidence=[("PO status", po.status)] + [(c.po_number, f"score {c.score:.2f}") for c in po.candidates],
                                refs=["po"]))

    # 7. own arithmetic
    a = f.arithmetic
    out.append(PolicyResult(
        rule_id="arithmetic", name="Invoice arithmetic", threshold=f"+/- {fmt_inr(_d(rules, 'arithmetic_tolerance_inr'))}",
        status="fail" if a.issues else "pass", impact=Decision.CORRECTION_REQUIRED if a.issues else None,
        detail=" ".join(i.message for i in a.issues) if a.issues else f"{a.checks_run} arithmetic checks passed.",
        evidence=[(i.code, f"expected {fmt_inr(i.expected)} vs invoice {fmt_inr(i.actual)}") for i in a.issues],
        refs=["arith"]))

    # 8-10. PO-dependent rules
    r = f.recon
    names = [("po_line_coverage", "PO line coverage"), ("quantity_vs_po", "Quantity vs PO"),
             ("price_tolerance", "Price / amount tolerance"), ("goods_receipt", "Goods receipt (3-way match)")]
    if r is None:
        why = f.recon_skipped_reason or "No confirmed PO, so PO/receipt checks could not run."
        out += [_skip(i, n, why) for i, n in names]
    else:
        out += _po_rules(f, r, rules)

    # 11-12. value rules
    hv = _d(rules, "high_value_threshold_inr")
    if inv.total is not None and inv.total > hv:
        out.append(PolicyResult(rule_id="high_value", name="High-value approval", threshold=f"> {fmt_inr(hv)}",
                                status="fail", impact=Decision.HUMAN_REVIEW,
                                detail=f"Invoice total {fmt_inr(inv.total)} exceeds the auto-approval limit {fmt_inr(hv)}.",
                                evidence=[("Total", fmt_inr(inv.total)), ("Limit", fmt_inr(hv))], refs=["invoice"]))
    else:
        out.append(PolicyResult(rule_id="high_value", name="High-value approval", threshold=f"> {fmt_inr(hv)}",
                                status="pass", detail="Within the auto-approval limit.", refs=["invoice"]))
    mult, minh = _d(rules, "unusual_amount_multiple"), int(rules.get("unusual_amount_min_history", 3))
    if inv.total is not None and f.vendor_avg_total and f.vendor_invoice_count >= minh and inv.total > mult * f.vendor_avg_total:
        out.append(PolicyResult(rule_id="unusual_amount", name="Unusual amount for vendor", threshold=f"> {mult}x vendor average",
                                status="fail", impact=Decision.HUMAN_REVIEW,
                                detail=f"{fmt_inr(inv.total)} is {inv.total / f.vendor_avg_total:.1f}x this vendor's average of {fmt_inr(f.vendor_avg_total)}.",
                                evidence=[("Vendor average", fmt_inr(f.vendor_avg_total)), ("History", f"{f.vendor_invoice_count} invoices")],
                                refs=["invoice"]))
    else:
        out.append(PolicyResult(rule_id="unusual_amount", name="Unusual amount for vendor", threshold=f"> {mult}x vendor average",
                                status="pass" if f.vendor_invoice_count >= minh else "skipped",
                                detail="In line with vendor history." if f.vendor_invoice_count >= minh
                                else "Not enough vendor history to judge.", refs=["invoice"]))
    return out


def _fq(x: Decimal | None) -> str:
    return "—" if x is None else format(x.normalize(), "f")


def _po_rules(f: PolicyFacts, r: ReconResult, rules: dict) -> list[PolicyResult]:
    res: list[PolicyResult] = []
    tol_amt, tol_qty, tol_rcpt = _d(rules, "amount_tolerance_pct"), _d(rules, "quantity_tolerance_pct"), _d(rules, "receipt_tolerance_pct")
    rcpt_action = _act(rules, "missing_receipt_action")
    cur_bad = bool(f.invoice.currency and f.po_currency and f.invoice.currency != f.po_currency)
    bad = r.unmatched_line_nos
    cov_fail = bool(bad) or cur_bad
    msgs = ([f"Invoice line(s) {', '.join(map(str, bad))} do not correspond to any PO item."] if bad else []) + \
           ([f"Currency {f.invoice.currency} differs from PO currency {f.po_currency}."] if cur_bad else [])
    res.append(PolicyResult(rule_id="po_line_coverage", name="PO line coverage", threshold="every line on the PO, same currency",
                            status="fail" if cov_fail else "pass", impact=Decision.HUMAN_REVIEW if cov_fail else None,
                            detail=" ".join(msgs) if msgs else "Every invoice line maps to a PO item.",
                            refs=[f"line:{n}" for n in bad] + ["po"]))

    over = [ln for ln in r.lines if "OVER_PO_QTY" in ln.flags]
    res.append(PolicyResult(
        rule_id="quantity_vs_po", name="Quantity vs PO (cumulative)", threshold=f"over-billing <= {tol_qty}%",
        status="fail" if over else "pass", impact=Decision.CORRECTION_REQUIRED if over else None,
        detail=("; ".join(f"{ln.sku or ln.description}: cumulative billed {_fq(ln.cumulative_invoiced_qty)} exceeds PO {_fq(ln.po_qty)} by {_fq(ln.qty_over_po)}" for ln in over)
                if over else "Cumulative billed quantity (previous invoices + this one) is within the PO quantity."),
        evidence=[x for ln in over for x in [(f"{ln.sku} PO qty", _fq(ln.po_qty)), (f"{ln.sku} prior invoiced", _fq(ln.prior_invoiced_qty)),
                                              (f"{ln.sku} this invoice", _fq(ln.invoice_qty))]],
        refs=[f"line:{ln.line_no}" for ln in over] + ["history"]))

    pv = [ln for ln in r.lines if "PRICE_VARIANCE" in ln.flags]
    tot_bad = r.total_variance_pct is not None and abs(r.total_variance_pct) > tol_amt
    pm = []
    if pv:
        pm.append("; ".join(f"{ln.sku}: invoice {fmt_inr(ln.invoice_unit_price)} vs PO {fmt_inr(ln.po_unit_price)} ({ln.price_variance_pct:+}%)" for ln in pv))
    if tot_bad:
        pm.append(f"Invoice total {fmt_inr(r.invoice_total)} vs {fmt_inr(r.expected_total)} expected at PO prices ({r.total_variance_pct:+}%).")
    res.append(PolicyResult(
        rule_id="price_tolerance", name="Price / amount tolerance", threshold=f"+/- {tol_amt}%",
        status="fail" if (pv or tot_bad) else "pass", impact=Decision.HOLD if (pv or tot_bad) else None,
        detail=" ".join(pm) if pm else (f"Prices match PO within {tol_amt}% (max line variance {r.max_abs_price_variance}%"
                                        + (f", total variance {r.total_variance_pct}%)." if r.total_variance_pct is not None else ").")),
        evidence=[("Expected total (PO prices)", fmt_inr(r.expected_total)), ("Invoice total", fmt_inr(r.invoice_total))] if tot_bad else [],
        refs=[f"line:{ln.line_no}" for ln in pv] + ["totals"]))

    short = [ln for ln in r.lines if "RECEIPT_SHORTFALL" in ln.flags]
    if r.receipt_count == 0 or short:
        if r.receipt_count == 0:
            det, ev = "No goods receipt exists for this PO - nothing billed has been received.", [("Goods received", "0")]
        else:
            det = "; ".join(f"{ln.sku or ln.description}: billed (cumulative) {_fq(ln.cumulative_invoiced_qty)} vs received {_fq(ln.received_qty)} -> {_fq(ln.receipt_shortfall)} unit(s) not received" for ln in short)
            ev = [x for ln in short for x in [("Invoice (this)", f"{_fq(ln.invoice_qty)} units"), ("PO", f"{_fq(ln.po_qty)} units"),
                                              ("Previously invoiced", f"{_fq(ln.prior_invoiced_qty)} units"), ("Goods received", f"{_fq(ln.received_qty)} units"),
                                              ("Variance", f"{_fq(ln.receipt_shortfall)} units")]]
        res.append(PolicyResult(rule_id="goods_receipt", name="Goods receipt (3-way match)", threshold=f"receipt tolerance = {tol_rcpt}%",
                                status="fail", impact=rcpt_action, detail=det, evidence=ev,
                                refs=[f"line:{ln.line_no}" for ln in short] + ["receipts"]))
    else:
        res.append(PolicyResult(rule_id="goods_receipt", name="Goods receipt (3-way match)", threshold=f"receipt tolerance = {tol_rcpt}%",
                                status="pass", detail="Everything billed (cumulatively) has been received.", refs=["receipts"]))
    return res


def decide(results: list[PolicyResult]) -> tuple[Decision, list[str]]:
    fails = [x for x in results if x.status == "fail" and x.impact]
    return most_restrictive([x.impact for x in fails]), [f"{x.name}: {x.detail}" for x in fails]


# ---- persistence / versioning -------------------------------------------------------------------
def get_active_policy(session: Session) -> m.Policy:
    p = session.scalar(select(m.Policy).where(m.Policy.is_active.is_(True)).order_by(m.Policy.version.desc()))
    if p is None:
        p = m.Policy(name=DEFAULT_POLICY_NAME, version=1, is_active=True, rules=dict(DEFAULT_RULES),
                     notes="Initial default policy", created_by="system")
        session.add(p)
        session.flush()
    return p


def publish_new_version(session: Session, rules: dict, notes: str, author: str) -> m.Policy:
    cur = get_active_policy(session)
    latest = session.scalar(select(m.Policy.version).where(m.Policy.name == cur.name).order_by(m.Policy.version.desc())) or cur.version
    session.execute(update(m.Policy).where(m.Policy.name == cur.name).values(is_active=False))
    p = m.Policy(name=cur.name, version=latest + 1, is_active=True, rules=rules, notes=notes, created_by=author)
    session.add(p)
    session.flush()
    return p


__all__ = ["DEFAULT_RULES", "PolicyFacts", "evaluate", "decide", "get_active_policy", "publish_new_version", "mask_account", "ZERO"]
