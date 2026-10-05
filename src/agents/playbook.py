"""Deterministic root-cause / action playbook. Used by the offline investigator and as the safe
fallback whenever the LLM investigator fails or returns something we cannot ground in evidence."""
from __future__ import annotations

from src.schemas.decision import SEVERITY, Decision, Finding, InvestigationReport, PolicyResult

PLAYBOOK: dict[str, tuple[str, str, str]] = {
    # rule_id: (root cause, recommended action, risk)
    "critical_data": ("The invoice is missing information required to process it.",
                      "Request a corrected invoice from the vendor containing the missing field(s).", "medium"),
    "ambiguous_extraction": ("One or more fields could not be read reliably, so the system refused to guess.",
                             "A reviewer should confirm the flagged fields against the original PDF.", "medium"),
    "vendor_known": ("The vendor on the invoice could not be matched confidently to the approved vendor master.",
                     "Verify the vendor identity (GSTIN, registration) before any payment; onboard if genuine.", "high"),
    "bank_account": ("The bank account on the invoice differs from the account(s) registered for this vendor - "
                     "a classic payment-diversion / business-email-compromise pattern.",
                     "Do NOT pay. Call the vendor on a known number to confirm any bank-detail change.", "high"),
    "duplicate": ("The invoice is identical or near-identical to one already in the system.",
                  "Do not pay; confirm with the vendor that this is a re-send and reject the duplicate.", "high"),
    "po_reference": ("The invoice cannot be tied to exactly one purchase order.",
                     "Confirm the correct PO with the requester/vendor, then re-run matching.", "medium"),
    "arithmetic": ("The invoice's own arithmetic does not add up (lines, tax or total).",
                   "Ask the vendor for a corrected invoice; do not pay a total that does not reconcile.", "medium"),
    "po_line_coverage": ("Invoice lines or currency do not correspond to the purchase order.",
                         "Check whether the PO needs an amendment or the vendor billed unordered items.", "medium"),
    "quantity_vs_po": ("Cumulative billed quantity exceeds the quantity ordered on the PO.",
                       "Request a corrected invoice or a PO amendment approved by the requester.", "medium"),
    "price_tolerance": ("Billed price/total deviates from the PO beyond the allowed tolerance.",
                        "Ask the vendor to justify the price difference or issue a credit note.", "medium"),
    "goods_receipt": ("Goods billed have not (fully) been received against the PO.",
                      "Verify the remaining delivery with the warehouse, or request a corrected invoice.", "medium"),
    "high_value": ("The invoice value exceeds the auto-approval limit.",
                   "Route to an authorised approver for sign-off.", "medium"),
    "unusual_amount": ("The amount is far outside this vendor's normal invoicing pattern.",
                       "Confirm the amount with the requester before paying.", "medium"),
}


def build_rule_based_report(policy_results: list[PolicyResult], engine_decision: Decision,
                            provider_label: str = "rule-based") -> InvestigationReport:
    fails = [r for r in policy_results if r.status == "fail"]
    warns = [r for r in policy_results if r.status == "warn"]
    if not fails and not warns:
        return InvestigationReport(
            summary="No exceptions found.", root_cause="None.", findings=[], risk_level="low",
            recommended_action="Release for payment per terms.", suggested_decision=Decision.APPROVE,
            confidence=0.9)
    fails.sort(key=lambda r: -SEVERITY[r.impact or Decision.HOLD])
    findings = []
    for r in fails + warns:
        sev = "high" if r.rule_id in ("bank_account", "duplicate", "vendor_known") else "medium"
        if r.status == "warn":
            sev = "low"
        findings.append(Finding(title=r.name, detail=r.detail, severity=sev, evidence_refs=r.evidence_ids))
    top = fails[0] if fails else warns[0]
    cause, action, risk = PLAYBOOK.get(top.rule_id, ("See findings.", "Review the findings.", "medium"))
    others = f" ({len(fails) - 1} further exception(s) also apply.)" if len(fails) > 1 else ""
    risk_level = "high" if any(f.severity == "high" for f in findings) else risk
    return InvestigationReport(
        summary=f"{len(fails)} policy exception(s) found. Primary issue: {top.name}.{others}",
        root_cause=cause, findings=findings, risk_level=risk_level,  # type: ignore[arg-type]
        risk_rationale=f"Driven by rule '{top.name}'.", recommended_action=action,
        suggested_decision=engine_decision, human_review_required=engine_decision == Decision.HUMAN_REVIEW,
        confidence=0.7,
        open_questions=[] if fails else ["Warnings only; no blocking rule failed."])
