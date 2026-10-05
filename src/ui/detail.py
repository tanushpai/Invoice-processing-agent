"""The Invoice Detail screen - the most important view. Everything shown is read from stored evidence."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pymupdf
import streamlit as st
from sqlalchemy.orm import Session

from src.core.money import fmt_inr, fmt_qty
from src.database import models as m
from src.ui.components import RULE_ICON, STAGE_ICON, badge, decision_hero, evidence_block


def _pdf_preview(inv: m.Invoice):
    if not inv.pdf_bytes:
        return None
    try:
        doc = pymupdf.open(stream=inv.pdf_bytes, filetype="pdf")
        return doc[0].get_pixmap(dpi=80).tobytes("png")
    except Exception:
        return None


def render_decision_card(inv: m.Invoice) -> None:
    d = inv.decision
    if d is None:
        st.warning("No decision recorded.")
        return
    facts, cat = d.facts or {}, (d.facts or {}).get("evidence_catalog", {})
    policy = f"Policy: AP Default v{d.policy_version}" if d.policy_version else ""
    sub = (d.reasons or ["All checks passed."])[0] if d.decision != "APPROVE" else "Every deterministic check passed."
    decision_hero(d.effective, policy, sub)
    if d.override_decision:
        st.info(f"🧑‍⚖️ Human override by **{d.reviewer}**: system said **{d.decision}**, final **{d.override_decision}**. Note: {d.review_note}")
    if facts.get("seeded"):
        st.caption("Seeded historical record - full evidence pack is only stored for invoices processed through the pipeline.")
        for r in d.reasons or []:
            st.write("•", r)
        return
    fails = [r for r in facts.get("policy_results", []) if r["status"] == "fail"]
    for r in fails:
        evidence_block(r, cat)
    if not fails and not facts.get("fatal"):
        st.success("No policy rule failed. Evidence for each check is listed in the Policy tab.")
    if facts.get("fatal"):
        st.error(f"Processing stopped safely: {facts['fatal']}")
    st.markdown("**Recommended action**")
    st.info(d.recommended_action or "—")
    if d.guard_notes:
        for n in d.guard_notes:
            st.caption(f"🛡️ Decision guard: {n}")


def render_invoice_detail(session: Session, invoice_id: int, show_header: bool = True) -> None:
    inv = session.get(m.Invoice, invoice_id)
    if inv is None:
        st.error("Invoice not found.")
        return
    d, facts = inv.decision, (inv.decision.facts if inv.decision else {}) or {}
    cat = facts.get("evidence_catalog", {})
    if show_header:
        st.markdown(f"## Invoice {inv.invoice_number or '(no number)'}  {badge(d.effective if d else inv.status)}", unsafe_allow_html=True)
        st.caption(f"{inv.vendor.name if inv.vendor else inv.vendor_name_raw or 'Unknown vendor'} · received {inv.received_at:%d %b %Y %H:%M} · "
                   f"file: {inv.source_filename} · extraction: {inv.extraction_method or '—'} via {inv.extraction_provider or '—'}")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total", fmt_inr(inv.total))
    c2.metric("Subtotal / Tax", f"{fmt_inr(inv.subtotal)} / {fmt_inr(inv.tax)}")
    c3.metric("PO", inv.po.po_number if inv.po else (inv.po_number_raw or "none"))
    c4.metric("Invoice date", str(inv.invoice_date or "—"))

    tabs = st.tabs(["⚖️ Decision & evidence", "📄 Extraction", "🔗 Matching", "🔢 Three-way match", "👯 Duplicates",
                    "🕵️ Investigation", "📜 Policy", "🕒 Audit trail"])
    with tabs[0]:
        left, right = st.columns([3, 1.4])
        with left:
            render_decision_card(inv)
        with right:
            img = _pdf_preview(inv)
            if img:
                st.image(img, caption="Original document (page 1)")
            if cat:
                with st.expander(f"Evidence catalog ({len(cat)})"):
                    for k, v in cat.items():
                        st.markdown(f"`{k}` {v}")

    with tabs[1]:
        meta = (inv.extraction_meta or {}).get("fields", {})
        if not meta:
            st.info("No extraction metadata stored for this record.")
        else:
            rows = []
            vals = {"vendor_name": inv.vendor_name_raw, "vendor_tax_id": inv.vendor_tax_id, "invoice_number": inv.invoice_number,
                    "invoice_date": inv.invoice_date, "due_date": inv.due_date, "service_start_date": inv.service_start_date,
                    "service_end_date": inv.service_end_date, "po_number": inv.po_number_raw, "currency": inv.currency,
                    "subtotal": inv.subtotal, "tax": inv.tax, "total": inv.total, "payment_terms": inv.payment_terms,
                    "bank_account": inv.bank_account}
            for k, mt in meta.items():
                rows.append({"canonical field": k, "value": "" if vals.get(k) is None else str(vals[k]),
                             "printed as": mt.get("raw_value") or "", "label on document": mt.get("source_label") or "",
                             "confidence": mt.get("confidence", 0.0), "review?": "⚠️" if mt.get("human_review_required") else "",
                             "warning": mt.get("warning") or ""})
            st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch",
                         column_config={"confidence": st.column_config.ProgressColumn("confidence", min_value=0, max_value=1, format="%.2f")})
            for w in (inv.extraction_meta or {}).get("warnings", []):
                st.warning(w)
        if inv.items:
            st.markdown("**Line items**")
            st.dataframe(pd.DataFrame([{"#": i.line_no, "description": i.description, "sku": i.sku, "qty": fmt_qty(i.quantity),
                                        "unit price": fmt_inr(i.unit_price), "GST %": i.tax_rate and str(i.tax_rate.normalize()),
                                        "tax": fmt_inr(i.tax_amount), "line total": fmt_inr(i.line_total),
                                        "mapped PO line": "✅" if i.po_item_id else "—"} for i in inv.items]),
                         hide_index=True, width="stretch")
        if inv.raw_text:
            with st.expander("Extracted text (what the AI actually saw)"):
                st.code(inv.raw_text, language=None)

    with tabs[2]:
        _matching_tab(facts)
    with tabs[3]:
        _three_way_tab(facts)
    with tabs[4]:
        dup = facts.get("duplicate")
        if not dup:
            st.info("No duplicate analysis stored.")
        else:
            (st.error if dup["is_duplicate"] else st.warning if dup["is_suspect"] else st.success)(
                f"Highest similarity to any existing invoice: **{dup['top_score']:.0%}**")
            for mt in dup["matches"]:
                with st.container(border=True):
                    st.markdown(f"**{mt['invoice_number']}** (id {mt['invoice_id']}) · {mt['vendor_name']} · total {fmt_inr(Decimal(mt['total'])) if mt['total'] not in (None, 'None') else '—'} · **{mt['score']:.0%}**")
                    st.caption(" · ".join(mt["signals"]))
                    if st.button("Open that invoice", key=f"dup_{mt['invoice_id']}"):
                        st.session_state["detail_id"] = mt["invoice_id"]
                        st.rerun()
            if inv.duplicate_of_id:
                st.caption(f"Linked as duplicate of invoice #{inv.duplicate_of_id}.")
    with tabs[5]:
        _investigation_tab(inv, cat)
    with tabs[6]:
        res = facts.get("policy_results", [])
        if not res:
            st.info("No policy evaluation stored.")
        else:
            st.caption(f"Evaluated under policy version **v{d.policy_version}**. Engine outcome: **{d.engine_decision}**.")
            st.dataframe(pd.DataFrame([{"": RULE_ICON[r["status"]], "rule": r["name"], "policy": r.get("threshold", ""),
                                        "if failed": (r.get("impact") or "").replace("_", " "), "result": r["detail"]} for r in res]),
                         hide_index=True, width="stretch")
    with tabs[7]:
        _audit_tab(inv, facts)


def _matching_tab(f: dict) -> None:
    if not f or "vendor" not in f:
        st.info("No matching evidence stored.")
        return
    v, b, po = f["vendor"], f.get("bank", {}), f.get("po", {})
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Vendor match**")
        (st.success if v["status"] == "matched" else st.warning)(f"{v['status'].upper()} · {v.get('vendor_name') or '—'} · {v['confidence']:.0%}")
        st.caption(f"via {v.get('method') or '—'}")
        for n in v.get("notes", []):
            st.caption(n)
        for c in v.get("candidates", [])[:3]:
            st.caption(f"candidate: {c['name']} ({c['score']:.2f}; {', '.join(c['methods'])})")
    with c2:
        st.markdown("**Bank account**")
        (st.success if b.get("status") == "match" else st.error if b.get("status") == "mismatch" else st.info)(b.get("status", "—").upper())
        st.caption(f"Invoice: {b.get('invoice_account_masked') or '—'} · Registered: {', '.join(b.get('registered_masked', [])) or '—'}")
        if b.get("note"):
            st.caption(b["note"])
    with c3:
        st.markdown("**PO match**")
        (st.success if po.get("status") == "explicit" else st.warning)(f"{po.get('status', '—').upper()} · {po.get('po_number') or '—'}")
        st.caption(po.get("note", ""))
        for c in po.get("candidates", []):
            st.caption(f"candidate {c['po_number']}: score {c['score']:.2f} — {'; '.join(c['reasons'])}")
    st.markdown("**Goods receipts on the PO**")
    rc = f.get("receipts", [])
    if rc:
        st.dataframe(pd.DataFrame([{"GRN": r["grn"], "date": r["date"], "items": ", ".join(f"{i['sku']} × {i['qty']}" for i in r["items"])} for r in rc]),
                     hide_index=True, width="stretch")
    else:
        st.caption("None recorded / PO not confirmed.")
    st.markdown("**Earlier invoices on the PO (split-invoice history)**")
    hs = f.get("history", [])
    if hs:
        st.dataframe(pd.DataFrame([{"invoice": h["invoice_number"], "date": h["date"], "status": h["status"],
                                    "billed": ", ".join(f"{k} × {v}" for k, v in h["qty"].items())} for h in hs]),
                     hide_index=True, width="stretch")
    else:
        st.caption("None.")


def _qf(ln: dict, k: str):
    return None if ln.get(k) is None else fmt_qty(Decimal(str(ln[k])))


def _three_way_tab(f: dict) -> None:
    r, a = f.get("recon"), f.get("arithmetic")
    if r is None:
        st.info(f.get("recon_skipped_reason") or "Three-way match not run.")
    else:
        rows = []
        for ln in r["lines"]:
            rows.append({"line": ln["line_no"], "sku": ln.get("sku"), "PO qty": _qf(ln, "po_qty"), "previously invoiced": _qf(ln, "prior_invoiced_qty"),
                         "this invoice": _qf(ln, "invoice_qty"), "cumulative": _qf(ln, "cumulative_invoiced_qty"), "received": _qf(ln, "received_qty"),
                         "not received": _qf(ln, "receipt_shortfall"), "over PO": _qf(ln, "qty_over_po"), "inv price": ln.get("invoice_unit_price"),
                         "PO price": ln.get("po_unit_price"), "price var %": ln.get("price_variance_pct"),
                         "flags": " ".join(f"🚩{x}" for x in ln["flags"]) or "✅"})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        c1, c2, c3 = st.columns(3)
        c1.metric("Expected total (PO prices)", fmt_inr(Decimal(r["expected_total"])) if r.get("expected_total") else "—")
        c2.metric("Invoice total", fmt_inr(Decimal(r["invoice_total"])) if r.get("invoice_total") else "—")
        c3.metric("Variance", f"{r['total_variance_pct']}%" if r.get("total_variance_pct") is not None else "—")
        if r.get("against_inferred_po"):
            st.warning("Reconciled against an INFERRED PO (none printed on the invoice). Treat as indicative only.")
        st.caption(f"Tolerances applied: amount ±{r['tolerances']['amount_pct']}%, quantity {r['tolerances']['qty_pct']}%, receipt {r['tolerances']['receipt_pct']}%. "
                   "Cumulative = earlier non-rejected invoices on the PO + this invoice.")
    if a:
        st.markdown("**Invoice arithmetic (does the invoice agree with itself?)**")
        if a["issues"]:
            for i in a["issues"]:
                st.error(i["message"])
        else:
            st.success(f"{a['checks_run']} checks passed (line maths, tax, subtotal, grand total).")


def _investigation_tab(inv: m.Invoice, cat: dict) -> None:
    iv = inv.investigation
    if iv is None:
        st.success("The investigation agent was not needed - no exceptions or ambiguity were found.")
        return
    st.caption(f"Engine: **{iv.provider}**" + (" (fallback - the AI could not be used or grounded)" if iv.fallback_used else ""))
    st.markdown(f"**Summary.** {iv.summary}")
    st.markdown(f"**Root cause.** {iv.root_cause}")
    st.markdown(f"**Risk: {str(iv.risk_level).upper()}.** {iv.risk_rationale or ''}")
    for f in iv.findings:
        with st.container(border=True):
            st.markdown(f"**{f.title}** · severity {f.severity}")
            st.write(f.detail)
            for e in f.evidence_refs or []:
                st.caption(f"`{e}` {cat.get(e, '')}")
    st.markdown(f"**Recommended action.** {iv.recommended_action}")
    st.caption(f"Suggested decision: {iv.suggested_decision} · human review requested: {iv.human_review_required} · confidence {float(iv.confidence or 0):.2f}")
    for q in iv.open_questions or []:
        st.caption(f"❓ {q}")
    for w in iv.warnings or []:
        st.warning(w)


def _audit_tab(inv: m.Invoice, facts: dict) -> None:
    stages = facts.get("stages", [])
    if stages:
        st.markdown("**Pipeline trace (actual workflow steps)**")
        st.dataframe(pd.DataFrame([{"": STAGE_ICON[s["status"]], "step": s["label"], "result": s["detail"], "ms": s["duration_ms"]} for s in stages]),
                     hide_index=True, width="stretch")
    st.markdown("**Audit timeline**")
    for e in inv.audit_events:
        st.markdown(f'<div class="tl"><b>{e.ts:%d %b %H:%M:%S}</b> · <code>{e.actor}</code> · {e.event_type}<br>{e.message}</div>', unsafe_allow_html=True)
