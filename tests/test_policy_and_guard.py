from decimal import Decimal as D

from src.decisions.guard import apply_guard
from src.matching.duplicates import DuplicateResult
from src.matching.po import POMatch
from src.matching.vendor import BankCheck, VendorMatch
from src.policies.engine import DEFAULT_RULES, PolicyFacts, decide, evaluate
from src.reconciliation.arithmetic import ArithmeticResult
from src.schemas.decision import Decision, InvestigationReport
from src.schemas.invoice import FieldMeta, NormalizedInvoice, NormalizedLineItem


def facts(**over):
    meta = {k: FieldMeta(confidence=0.95) for k in ("vendor_name", "invoice_number", "invoice_date", "total")}
    inv = NormalizedInvoice(vendor_name="V", invoice_number="1", invoice_date="2026-01-01", total=D(1000), meta=meta,
                            line_items=[NormalizedLineItem(line_no=1, quantity=D(1), unit_price=D(1000))])
    base = dict(invoice=inv, vendor=VendorMatch(status="matched", vendor_id=1, vendor_name="V", confidence=1, method="tax_id"),
                bank=BankCheck(status="match"), po=POMatch(status="explicit", po_id=1, po_number="PO-1"),
                arithmetic=ArithmeticResult(checks_run=3), duplicate=DuplicateResult(), recon=None, recon_skipped_reason="n/a")
    base.update(over)
    return PolicyFacts(**base)


def outcome(f, rules=None):
    return decide(evaluate(f, rules or DEFAULT_RULES))[0]


def test_clean_invoice_approves():
    assert outcome(facts()) == Decision.APPROVE


def test_bank_mismatch_human_review():
    assert outcome(facts(bank=BankCheck(status="mismatch", invoice_account_masked="1", note="x"))) == Decision.HUMAN_REVIEW


def test_missing_po_human_review():
    assert outcome(facts(po=POMatch(status="missing", note="none"))) == Decision.HUMAN_REVIEW


def test_duplicate_thresholds():
    from src.matching.duplicates import DuplicateMatch
    m = DuplicateMatch(invoice_id=1, invoice_number="A", vendor_name="V", total="1", score=0.95, signals=["same vendor"])
    assert outcome(facts(duplicate=DuplicateResult(is_duplicate=True, top_score=0.95, matches=[m]))) == Decision.REJECT
    m2 = m.model_copy(update={"score": 0.7})
    assert outcome(facts(duplicate=DuplicateResult(is_suspect=True, top_score=0.7, matches=[m2]))) == Decision.HUMAN_REVIEW


def test_policy_is_configurable():
    f = facts(invoice=facts().invoice.model_copy(update={"total": D(600000)}))
    assert outcome(f) == Decision.HUMAN_REVIEW
    assert outcome(f, {**DEFAULT_RULES, "high_value_threshold_inr": 10_000_000}) == Decision.APPROVE


def test_most_restrictive_wins():
    f = facts(bank=BankCheck(status="mismatch", note="x"), arithmetic=ArithmeticResult(issues=[]))
    f.invoice.meta["total"] = FieldMeta(confidence=0.2)
    assert outcome(f) == Decision.HUMAN_REVIEW


def report(decision, review=False):
    return InvestigationReport(summary="s", root_cause="r", recommended_action="a", suggested_decision=decision, human_review_required=review)


def test_guard_blocks_ai_approve_when_rules_failed():
    res = evaluate(facts(bank=BankCheck(status="mismatch", note="x")), DEFAULT_RULES)
    g = apply_guard(Decision.HUMAN_REVIEW, res, report(Decision.APPROVE))
    assert g.final == Decision.HUMAN_REVIEW and any("overridden" in n for n in g.notes)


def test_guard_allows_escalation_but_never_ai_reject():
    res = evaluate(facts(), DEFAULT_RULES)
    assert apply_guard(Decision.APPROVE, res, report(Decision.HUMAN_REVIEW)).final == Decision.HUMAN_REVIEW
    assert apply_guard(Decision.APPROVE, res, report(Decision.REJECT)).final == Decision.HUMAN_REVIEW
