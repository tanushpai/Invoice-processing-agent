"""Investigation Agent: runs only when there is an exception or ambiguity.
Python gathers extra evidence; the model explains using ONLY the evidence catalog; output is validated
and every finding must cite real evidence ids (ungrounded findings are dropped)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.agents.evidence import EvidenceBook
from src.agents.playbook import build_rule_based_report
from src.ai.provider import AIProvider, AIProviderError
from src.database import models as m
from src.policies.engine import PolicyFacts
from src.schemas.decision import Decision, InvestigationReport, PolicyResult


@dataclass
class InvestigationOutcome:
    report: InvestigationReport
    provider: str
    fallback_used: bool = False
    warnings: list[str] = field(default_factory=list)


def needs_investigation(results: list[PolicyResult], facts: PolicyFacts) -> bool:
    return any(r.status in ("fail", "warn") for r in results) or bool(facts.invoice.warnings)


def gather_extra_evidence(session: Session, f: PolicyFacts, book: EvidenceBook, results: list[PolicyResult]) -> None:
    """Targeted, deterministic follow-up queries - the 'detective legwork' - before the model sees anything."""
    failed = {r.rule_id for r in results if r.status == "fail"}
    inv = f.invoice
    if "goods_receipt" in failed and f.po.po_id:
        later = []
        if inv.invoice_date:
            later = session.scalars(select(m.GoodsReceipt).where(
                m.GoodsReceipt.po_id == f.po.po_id, m.GoodsReceipt.received_date > inv.invoice_date)).all()
        book.add("receipts", "Follow-up query: " + (
            "receipts dated after the invoice: " + ", ".join(r.grn_number for r in later) if later
            else "no later goods receipt exists for this PO, so the missing quantity has not arrived."))
    if "bank_account" in failed and f.vendor.vendor_id:
        n = len(session.scalars(select(m.VendorBankAccount).where(m.VendorBankAccount.vendor_id == f.vendor.vendor_id)).all())
        book.add("bank", f"Follow-up query: the vendor master holds {n} registered account(s); no pending bank-change request exists in the system.")
    if f.po.status in ("missing", "ambiguous", "not_found", "inferred") and f.vendor.vendor_id:
        open_pos = session.scalars(select(m.PurchaseOrder).where(
            m.PurchaseOrder.vendor_id == f.vendor.vendor_id, m.PurchaseOrder.status == "open")).all()
        book.add("po", f"Follow-up query: vendor has {len(open_pos)} open PO(s): {', '.join(p.po_number for p in open_pos) or 'none'}.")
    if "duplicate" in failed and f.duplicate.matches:
        t = f.duplicate.matches[0]
        orig = session.get(m.Invoice, t.invoice_id)
        if orig is not None:
            book.add("dup", f"Follow-up query: the earlier invoice was received {orig.received_at:%Y-%m-%d} and is currently {orig.status}.")


def build_llm_facts(f: PolicyFacts, results: list[PolicyResult], engine_decision: Decision) -> dict:
    return {
        "engine_decision": engine_decision.value,
        "invoice": f.invoice.model_dump(mode="json", exclude={"meta"}),
        "field_confidence": {k: {"confidence": v.confidence, "warning": v.warning, "review": v.human_review_required}
                             for k, v in f.invoice.meta.items()},
        "vendor_match": f.vendor.model_dump(mode="json"), "bank_check": f.bank.model_dump(mode="json"),
        "po_match": f.po.model_dump(mode="json"),
        "reconciliation": f.recon.model_dump(mode="json") if f.recon else None,
        "arithmetic": f.arithmetic.model_dump(mode="json"), "duplicates": f.duplicate.model_dump(mode="json"),
        "policy_results": [r.model_dump(mode="json") for r in results],
    }


def _ground(report: InvestigationReport, catalog: dict[str, str]) -> tuple[InvestigationReport, int]:
    kept, dropped = [], 0
    for fd in report.findings:
        refs = [r for r in (re.sub(r"[^E0-9]", "", x) for x in fd.evidence_refs) if r in catalog]
        if refs:
            fd.evidence_refs = refs
            kept.append(fd)
        else:
            dropped += 1
    report.findings = kept
    return report, dropped


def run_investigation(provider: AIProvider, f: PolicyFacts, results: list[PolicyResult],
                      engine_decision: Decision, book: EvidenceBook) -> InvestigationOutcome:
    facts = build_llm_facts(f, results, engine_decision)
    fallback = lambda why: InvestigationOutcome(  # noqa: E731
        report=build_rule_based_report(results, engine_decision), provider="rule-based fallback",
        fallback_used=True, warnings=[why])
    try:
        report = provider.investigate(facts, book.entries)
    except AIProviderError as e:
        return fallback(f"AI investigator unavailable ({e}); deterministic explanation used instead.")
    report, dropped = _ground(report, book.entries)
    warns = [f"{dropped} finding(s) dropped: they cited no valid evidence id."] if dropped else []
    if not report.findings and any(r.status == "fail" for r in results):
        out = fallback("AI findings could not be grounded in evidence; deterministic explanation used instead.")
        out.warnings = warns + out.warnings
        return out
    return InvestigationOutcome(report=report, provider=provider.name, warnings=warns)
