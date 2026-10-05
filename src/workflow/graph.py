"""LangGraph workflow. Every node is a real processing step and emits exactly one StageEvent,
so the live progress shown in the UI is the actual execution trace - nothing is faked."""
from __future__ import annotations

import hashlib
import operator
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.agents.evidence import build_evidence_book
from src.agents.investigation import gather_extra_evidence, needs_investigation, run_investigation
from src.agents.playbook import build_rule_based_report
from src.ai.provider import AIProvider, AIProviderError
from src.audit.log import event
from src.core.config import Settings
from src.core.errors import PipelineHalt
from src.core.money import fmt_qty
from src.database import models as m
from src.decisions.guard import apply_guard
from src.document.extract import EmptyPDFError, ExtractionResult, InvalidPDFError, OCRError, extract_text
from src.document.normalize import content_hash, normalize_extraction
from src.matching.duplicates import detect_duplicates
from src.matching.po import load_po_items, map_lines_to_po_items, match_po, prior_invoiced_by_item
from src.matching.vendor import check_bank, match_vendor
from src.policies.engine import PolicyFacts, decide, evaluate, get_active_policy
from src.reconciliation.arithmetic import check_invoice_arithmetic
from src.reconciliation.three_way import Tolerances, reconcile
from src.schemas.decision import Decision, StageEvent


class PipelineState(TypedDict, total=False):
    file_bytes: bytes
    filename: str
    file_hash: str
    policy_id: int
    policy_name: str
    policy_version: int
    rules: dict
    extraction: ExtractionResult
    use_vision: bool
    invoice: Any
    content_hash: str | None
    vendor: Any
    bank: Any
    po: Any
    po_items: list
    mapping: dict
    receipts: list
    receipts_by_item: dict
    prior_by_item: dict
    history: list
    duplicate: Any
    arithmetic: Any
    recon: Any
    recon_skipped_reason: str | None
    facts: Any
    policy_results: list
    engine_decision: Decision
    reasons: list
    book: Any
    investigation: Any
    guard: Any
    final_decision: Decision
    recommended_action: str
    fatal: str | None
    invoice_id: int
    stages: Annotated[list, operator.add]
    audit: Annotated[list, operator.add]


@dataclass
class RunContext:
    session: Session
    provider: AIProvider
    settings: Settings


def _stage(key: str, label: str, actor: str = "system"):
    """Wrap a node: time it, catch failures (-> fatal, routed to HUMAN_REVIEW), emit stage + audit event."""
    def deco(fn):
        def run(state: PipelineState, ctx: RunContext | None = None):
            t = time.perf_counter()
            try:
                update, status, detail = fn(state, ctx)
            except PipelineHalt as e:
                update, status, detail = {"fatal": str(e)}, "error", str(e)
            except Exception as e:  # never crash the run; never fabricate - route to a human
                ctx.session.rollback()
                update, status, detail = {"fatal": f"{type(e).__name__}: {e}"}, "error", f"Unexpected error: {type(e).__name__}: {e}"
            ms = int((time.perf_counter() - t) * 1000)
            update = dict(update)
            payload = update.pop("_audit", None)
            update["stages"] = [StageEvent(key=key, label=label, status=status, detail=detail, duration_ms=ms)]
            update["audit"] = [event(actor, key, detail, payload)]
            return update
        run.__name__ = fn.__name__
        return run
    return deco


# ---------------------------------------------------------------------------------------------------
def build_graph(ctx: RunContext):
    S = ctx.settings

    @_stage("receive", "Document received")
    def n_receive(st, ctx):
        data = st["file_bytes"]
        if not data:
            raise PipelineHalt("The uploaded file is empty (0 bytes).")
        if len(data) > S.max_upload_mb * 1024 * 1024:
            raise PipelineHalt(f"File exceeds the {S.max_upload_mb} MB limit.")
        pol = get_active_policy(ctx.session)
        h = hashlib.sha256(data).hexdigest()
        return ({"file_hash": h, "policy_id": pol.id, "policy_name": pol.name, "policy_version": pol.version,
                 "rules": dict(pol.rules)}, "ok",
                f"{st['filename']} - {len(data) / 1024:.0f} KB - sha256 {h[:12]}... - policy {pol.name} v{pol.version} loaded")

    @_stage("extract_text", "Text extracted")
    def n_extract(st, ctx):
        try:
            ex = extract_text(st["file_bytes"], S.min_text_chars_per_page, S.ocr_dpi)
        except OCRError as e:
            if ctx.provider.supports_vision:
                ex = ExtractionResult(text="", method="vision", page_count=0, char_count=0,
                                      warnings=[f"OCR unavailable ({e}); the PDF is read directly by the vision model."])
                return {"extraction": ex, "use_vision": True}, "warn", ex.warnings[0]
            raise PipelineHalt(f"Scanned PDF and OCR is unavailable: {e}") from e
        except (InvalidPDFError, EmptyPDFError) as e:
            raise PipelineHalt(str(e)) from e
        if ex.method == "ocr":
            c = f", mean confidence {ex.ocr_confidence:.0%}" if ex.ocr_confidence is not None else ""
            return {"extraction": ex}, "ok", f"Scanned PDF detected - no text layer - OCR (Tesseract) read {ex.char_count} chars from {ex.page_count} page(s){c}"
        return {"extraction": ex}, "ok", f"{ex.char_count} chars from {ex.page_count} page(s) via PyMuPDF text layer (no OCR needed)"

    @_stage("understand", "Invoice understood", actor="ai")
    def n_understand(st, ctx):
        ex: ExtractionResult = st["extraction"]
        try:
            raw = ctx.provider.extract_invoice(ex.text, st["file_bytes"] if st.get("use_vision") else None)
        except AIProviderError as e:
            raise PipelineHalt(f"AI extraction failed ({e}). Cannot safely continue without understanding the invoice.") from e
        inv = normalize_extraction(raw)
        inv.warnings += ex.warnings
        flagged = inv.review_fields(float(st["rules"].get("min_field_confidence", 0.7)))
        got = sum(1 for k in inv.meta if getattr(inv, k) is not None)
        d = f"{ctx.provider.name}: {got}/{len(inv.meta)} fields, {len(inv.line_items)} line item(s)"
        if flagged:
            d += f"; {len(flagged)} flagged for review ({', '.join(flagged)})"
        return ({"invoice": inv, "content_hash": content_hash(ex.text) if ex.text else None,
                 "_audit": {"flagged": flagged}}, "warn" if flagged else "ok", d)

    @_stage("match_vendor", "Vendor matched")
    def n_vendor(st, ctx):
        inv = st["invoice"]
        vm = match_vendor(ctx.session, inv.vendor_name, inv.vendor_tax_id, inv.bank_account)
        bk = check_bank(ctx.session, vm.vendor_id, inv.bank_account)
        if vm.status != "matched":
            return {"vendor": vm, "bank": bk}, "warn", f"Vendor {vm.status.replace('_', ' ')}: {vm.notes[0] if vm.notes else ''}"
        bank_txt = {"match": "bank account verified", "mismatch": "BANK ACCOUNT NOT REGISTERED",
                    "not_provided": "no bank account on invoice", "no_registered_account": "no registered account"}.get(bk.status, bk.status)
        return ({"vendor": vm, "bank": bk}, "warn" if bk.status == "mismatch" else "ok",
                f"{vm.vendor_name} via {vm.method} ({vm.confidence:.0%}) - {bank_txt}")

    @_stage("match_po", "PO matched")
    def n_po(st, ctx):
        po = match_po(ctx.session, st["invoice"], st["vendor"].vendor_id)
        label = po.po_number or "-"
        return {"po": po}, ("ok" if po.status == "explicit" else "warn"), f"{po.status.upper()} {label}: {po.note}"

    @_stage("check_receipts", "Receipts checked")
    def n_receipts(st, ctx):
        po = st["po"]
        if not po.usable_for_recon:
            return {"po_items": [], "mapping": {}, "receipts": [], "history": [], "receipts_by_item": {}, "prior_by_item": {}}, \
                "skipped", "No single confirmed PO - receipts and history not checked"
        items = load_po_items(ctx.session, po.po_id)
        mapping = map_lines_to_po_items(st["invoice"], items)
        sku = {i.id: i.sku for i in items}
        grs = ctx.session.scalars(select(m.GoodsReceipt).where(m.GoodsReceipt.po_id == po.po_id)
                                  .order_by(m.GoodsReceipt.received_date, m.GoodsReceipt.id)).all()
        by_item: dict[int, Decimal] = defaultdict(Decimal)
        receipts = []
        for g in grs:
            its = []
            for gi in g.items:
                by_item[gi.po_item_id] += gi.quantity_received
                its.append({"sku": sku.get(gi.po_item_id, "?"), "qty": fmt_qty(gi.quantity_received)})
            receipts.append({"grn": g.grn_number, "date": str(g.received_date), "items": its})
        prior = prior_invoiced_by_item(ctx.session, po.po_id)
        hist = []
        for inv in ctx.session.scalars(select(m.Invoice).where(m.Invoice.po_id == po.po_id, m.Invoice.status != "REJECT")
                                       .order_by(m.Invoice.invoice_date, m.Invoice.id)).all():
            q = defaultdict(Decimal)
            for it in inv.items:
                if it.po_item_id:
                    q[sku.get(it.po_item_id, "?")] += it.quantity or 0
            hist.append({"invoice_id": inv.id, "invoice_number": inv.invoice_number, "status": inv.status,
                         "date": str(inv.invoice_date), "qty": {k: fmt_qty(v) for k, v in q.items()}})
        return ({"po_items": items, "mapping": mapping, "receipts": receipts, "history": hist,
                 "receipts_by_item": dict(by_item), "prior_by_item": prior}, "ok" if receipts else "warn",
                f"{len(receipts)} goods receipt(s) and {len(hist)} earlier invoice(s) found on {po.po_number}")

    @_stage("check_duplicates", "Duplicate checked")
    def n_dups(st, ctx):
        r, po = st["rules"], st["po"]
        dup = detect_duplicates(ctx.session, st["invoice"], st["vendor"].vendor_id, po.po_id if po.usable_for_recon else None,
                                st["file_hash"], st.get("content_hash"), float(r.get("duplicate_reject_threshold", .9)),
                                float(r.get("duplicate_suspect_threshold", .6)))
        if dup.is_duplicate:
            return {"duplicate": dup}, "warn", f"DUPLICATE: {dup.top_score:.0%} similar to {dup.matches[0].invoice_number} ({'; '.join(dup.matches[0].signals)})"
        if dup.is_suspect:
            return {"duplicate": dup}, "warn", f"Possible duplicate: {dup.top_score:.0%} similar to {dup.matches[0].invoice_number}"
        return {"duplicate": dup}, "ok", f"No duplicate found (highest similarity {dup.top_score:.0%})"

    @_stage("reconcile", "Reconciliation completed")
    def n_recon(st, ctx):
        r, inv, po = st["rules"], st["invoice"], st["po"]
        arith = check_invoice_arithmetic(inv, Decimal(str(r.get("arithmetic_tolerance_inr", 1.0))))
        parts = [f"arithmetic {arith.checks_run} checks, {len(arith.issues)} issue(s)"]
        recon, why = None, None
        if st["duplicate"].is_duplicate:
            why = "Skipped three-way match: invoice is a confirmed duplicate."
        elif not po.usable_for_recon:
            why = "No single confirmed PO, so the three-way match could not run."
        else:
            tol = Tolerances(Decimal(str(r.get("amount_tolerance_pct", 2))), Decimal(str(r.get("quantity_tolerance_pct", 0))),
                             Decimal(str(r.get("receipt_tolerance_pct", 0))))
            recon = reconcile(inv, st["po_items"], st["mapping"], st["receipts_by_item"], st["prior_by_item"], tol,
                              len(st["receipts"]), against_inferred_po=po.status == "inferred")
            flagged = sum(1 for x in recon.lines if x.flags)
            parts.append(f"3-way match {len(recon.lines)} line(s), {flagged} flagged")
        if why:
            parts.append(why)
        bad = bool(arith.issues) or (recon is not None and any(x.flags for x in recon.lines))
        return {"arithmetic": arith, "recon": recon, "recon_skipped_reason": why}, ("warn" if bad else "ok" if recon else "skipped"), "; ".join(parts)

    @_stage("evaluate_policy", "Policy evaluated")
    def n_policy(st, ctx):
        vm, po = st["vendor"], st["po"]
        avg, cnt = None, 0
        if vm.vendor_id:
            row = ctx.session.execute(select(func.avg(m.Invoice.total), func.count(m.Invoice.id)).where(
                m.Invoice.vendor_id == vm.vendor_id, m.Invoice.status == "APPROVE")).one()
            avg, cnt = (Decimal(str(row[0])) if row[0] is not None else None), int(row[1])
        po_cur = ctx.session.get(m.PurchaseOrder, po.po_id).currency if po.po_id else None
        facts = PolicyFacts(invoice=st["invoice"], vendor=vm, bank=st["bank"], po=po, po_currency=po_cur, recon=st["recon"],
                            recon_skipped_reason=st.get("recon_skipped_reason"), arithmetic=st["arithmetic"],
                            duplicate=st["duplicate"], vendor_avg_total=avg, vendor_invoice_count=cnt)
        results = evaluate(facts, st["rules"])
        eng, reasons = decide(results)
        book = build_evidence_book(facts, f"{st['policy_name']} v{st['policy_version']}", st["receipts"], st["history"])
        for r in results:
            r.evidence_ids = book.ids(r.refs)
        fails = [r.name for r in results if r.status == "fail"]
        return ({"facts": facts, "policy_results": results, "engine_decision": eng, "reasons": reasons, "book": book,
                 "_audit": {"policy_version": st["policy_version"], "failed": fails}}, "warn" if fails else "ok",
                f"{sum(r.status == 'pass' for r in results)} rules passed, {len(fails)} failed"
                + (f" ({', '.join(fails)})" if fails else "") + f" -> policy outcome {eng.value}")

    @_stage("investigate", "Investigation completed", actor="ai")
    def n_investigate(st, ctx):
        facts, results, book = st["facts"], st["policy_results"], st["book"]
        if not needs_investigation(results, facts):
            return {"investigation": None}, "skipped", "No exceptions or ambiguity - investigation agent not needed"
        gather_extra_evidence(ctx.session, facts, book, results)
        out = run_investigation(ctx.provider, facts, results, st["engine_decision"], book)
        rep = out.report
        d = f"{out.provider}: {len(rep.findings)} finding(s), risk {rep.risk_level}, suggests {rep.suggested_decision.value}"
        if out.warnings:
            d += " - " + " ".join(out.warnings)
        return ({"investigation": out, "_audit": {"provider": out.provider, "fallback": out.fallback_used}},
                "warn" if out.fallback_used else "ok", d)

    @_stage("decide", "Decision generated")
    def n_decide(st, ctx):
        if st.get("fatal"):
            return ({"final_decision": Decision.HUMAN_REVIEW, "engine_decision": Decision.HUMAN_REVIEW,
                     "reasons": [st["fatal"]], "policy_results": st.get("policy_results", []),
                     "recommended_action": "Processing stopped safely. Inspect the document manually or ask the vendor to resend a readable PDF."},
                    "error", f"HUMAN_REVIEW - processing could not continue safely: {st['fatal']}")
        results, eng, out = st["policy_results"], st["engine_decision"], st.get("investigation")
        g = apply_guard(eng, results, out.report if out else None)
        if g.final == Decision.APPROVE:
            inv = st["invoice"]
            action = (f"Approve and schedule payment for {inv.due_date}." if inv.due_date else
                      f"Approve and schedule payment per vendor terms ({inv.payment_terms or 'standard terms'}).")
        elif out and out.report.suggested_decision == g.final:
            action = out.report.recommended_action
        else:
            action = build_rule_based_report(results, g.final).recommended_action
        return ({"guard": g, "final_decision": g.final, "recommended_action": action,
                 "_audit": {"engine": eng.value, "ai": g.ai_suggested.value if g.ai_suggested else None, "guard_notes": g.notes}},
                "ok", f"{g.final.value} (policy {st['policy_name']} v{st['policy_version']})" + (f" - guard: {' '.join(g.notes)}" if g.notes else ""))

    @_stage("persist", "Saved with audit trail")
    def n_persist(st, ctx):
        iid = _persist(ctx, st)
        return {"invoice_id": iid}, "ok", f"Invoice #{iid} stored with evidence, decision and audit trail"

    nodes = [("n_receive", n_receive), ("n_extract", n_extract), ("n_understand", n_understand), ("n_vendor", n_vendor),
             ("n_po", n_po), ("n_receipts", n_receipts), ("n_dups", n_dups), ("n_recon", n_recon), ("n_policy", n_policy),
             ("n_investigate", n_investigate), ("n_decide", n_decide), ("n_persist", n_persist)]
    g = StateGraph(PipelineState)
    for name, fn in nodes:
        g.add_node(name, lambda s, _f=fn: _f(s, ctx))
    g.add_edge(START, "n_receive")
    names = [n for n, _ in nodes]
    for i, name in enumerate(names[:-2]):  # everything before decide can halt -> decide
        g.add_conditional_edges(name, lambda s: "halt" if s.get("fatal") else "next",
                                {"halt": "n_decide", "next": names[i + 1]})
    g.add_edge("n_decide", "n_persist")
    g.add_edge("n_persist", END)
    return g.compile()


# ---------------------------------------------------------------------------------------------------
def _persist(ctx: RunContext, st: PipelineState) -> int:
    s = ctx.session
    inv, ex, vm, po = st.get("invoice"), st.get("extraction"), st.get("vendor"), st.get("po")
    final: Decision = st["final_decision"]
    dup = st.get("duplicate")
    explicit = bool(po and po.status == "explicit")
    row = m.Invoice(
        invoice_number=inv.invoice_number if inv else None, vendor_id=vm.vendor_id if vm else None,
        vendor_name_raw=inv.vendor_name if inv else None, vendor_tax_id=inv.vendor_tax_id if inv else None,
        invoice_date=inv.invoice_date if inv else None, due_date=inv.due_date if inv else None,
        service_start_date=inv.service_start_date if inv else None, service_end_date=inv.service_end_date if inv else None,
        po_id=po.po_id if explicit else None, po_number_raw=inv.po_number if inv else None,
        currency=inv.currency if inv else None, subtotal=inv.subtotal if inv else None, tax=inv.tax if inv else None,
        total=inv.total if inv else None, payment_terms=inv.payment_terms if inv else None,
        bank_account=inv.bank_account if inv else None, source_filename=st.get("filename"), file_hash=st.get("file_hash"),
        content_hash=st.get("content_hash"), extraction_method=ex.method if ex else None,
        extraction_provider=ctx.provider.name,
        extraction_meta=({"fields": {k: v.model_dump(mode="json") for k, v in inv.meta.items()}, "warnings": inv.warnings,
                          "ocr_confidence": ex.ocr_confidence if ex else None} if inv else None),
        raw_text=ex.text if ex else None, pdf_bytes=st.get("file_bytes"), status=final.value, source="upload",
        duplicate_of_id=dup.matches[0].invoice_id if dup and dup.is_duplicate else None)
    if inv:
        for li in inv.line_items:
            row.items.append(m.InvoiceItem(
                line_no=li.line_no, description=li.description, sku=li.sku, quantity=li.quantity, unit_price=li.unit_price,
                tax_rate=li.tax_rate, tax_amount=li.tax_amount, line_total=li.line_total,
                po_item_id=(st.get("mapping") or {}).get(li.line_no) if explicit else None))
    s.add(row)
    s.flush()

    results, guard, out = st.get("policy_results", []), st.get("guard"), st.get("investigation")
    facts_json: dict = {"stages": [e.model_dump(mode="json") for e in st.get("stages", [])],
                        "policy_results": [r.model_dump(mode="json") for r in results],
                        "evidence_catalog": st["book"].entries if st.get("book") else {},
                        "fatal": st.get("fatal")}
    for key in ("vendor", "bank", "po", "duplicate", "arithmetic", "recon"):
        if st.get(key) is not None:
            facts_json[key] = st[key].model_dump(mode="json")
    facts_json.update({"receipts": st.get("receipts", []), "history": st.get("history", []),
                       "recon_skipped_reason": st.get("recon_skipped_reason")})
    s.add(m.Decision(
        invoice_id=row.id, decision=final.value, engine_decision=st["engine_decision"].value if st.get("engine_decision") else None,
        ai_suggested_decision=guard.ai_suggested.value if guard and guard.ai_suggested else None,
        guard_notes=guard.notes if guard else [], policy_id=st.get("policy_id"), policy_version=st.get("policy_version"),
        reasons=st.get("reasons", []), recommended_action=st.get("recommended_action"), facts=facts_json,
        review_status="pending" if final == Decision.HUMAN_REVIEW else "none"))
    if out:
        r = out.report
        inv_row = m.Investigation(
            invoice_id=row.id, provider=out.provider, fallback_used=out.fallback_used, summary=r.summary,
            root_cause=r.root_cause, risk_level=r.risk_level, risk_rationale=r.risk_rationale,
            recommended_action=r.recommended_action, suggested_decision=r.suggested_decision.value,
            human_review_required=r.human_review_required, confidence=r.confidence, open_questions=r.open_questions,
            warnings=out.warnings)
        for f in r.findings:
            inv_row.findings.append(m.InvestigationFinding(title=f.title, detail=f.detail, severity=f.severity,
                                                           evidence_refs=f.evidence_refs))
        s.add(inv_row)
    for a in st.get("audit", []):
        s.add(m.AuditEvent(invoice_id=row.id, ts=a["ts"], actor=a["actor"], event_type=a["event_type"],
                           message=a["message"], payload=a.get("payload")))
    s.add(m.AuditEvent(invoice_id=row.id, actor="system", event_type="final_decision",
                       message=f"Final decision {final.value} under {st.get('policy_name')} v{st.get('policy_version')}",
                       payload={"decision": final.value, "policy_version": st.get("policy_version")}))
    s.commit()
    return row.id


def run_pipeline(session: Session, provider: AIProvider, settings: Settings, pdf_bytes: bytes,
                 filename: str) -> Iterator[tuple[str, Any]]:
    """Yields ('stage', StageEvent) as each real workflow node finishes, then ('done', final_state)."""
    graph = build_graph(RunContext(session, provider, settings))
    seen, last = 0, None
    for state in graph.stream({"file_bytes": pdf_bytes, "filename": filename, "stages": [], "audit": []}, stream_mode="values"):
        last = state
        stages = state.get("stages", [])
        for ev in stages[seen:]:
            yield "stage", ev
        seen = len(stages)
    yield "done", last
