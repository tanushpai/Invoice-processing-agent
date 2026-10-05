"""Evidence catalog: deterministic, human-readable facts with stable ids (E1, E2...).
The investigation agent may only cite these ids; the UI shows them next to every decision."""
from __future__ import annotations

from collections import defaultdict

from src.core.money import fmt_inr
from src.policies.engine import PolicyFacts


def _q(x) -> str:
    return "—" if x is None else format(x.normalize(), "f")


class EvidenceBook:
    def __init__(self) -> None:
        self.entries: dict[str, str] = {}
        self._topics: dict[str, list[str]] = defaultdict(list)

    def add(self, topic: str, text: str) -> str:
        eid = f"E{len(self.entries) + 1}"
        self.entries[eid] = text
        self._topics[topic].append(eid)
        return eid

    def ids(self, topics: list[str]) -> list[str]:
        seen: list[str] = []
        for t in topics:
            for i in self._topics.get(t, []):
                if i not in seen:
                    seen.append(i)
        return seen


def build_evidence_book(f: PolicyFacts, policy_label: str, receipts: list[dict], history: list[dict]) -> EvidenceBook:
    b, inv = EvidenceBook(), f.invoice
    b.add("invoice", f"Invoice {inv.invoice_number or '(no number)'} from {inv.vendor_name or '(unknown vendor)'} dated "
                     f"{inv.invoice_date or '(no date)'}: subtotal {fmt_inr(inv.subtotal)}, tax {fmt_inr(inv.tax)}, total {fmt_inr(inv.total)}.")
    for name, meta in inv.meta.items():
        if meta.human_review_required or meta.warning and getattr(inv, name) is None and meta.confidence > 0:
            b.add(f"extraction:{name}", f"Field '{name}' printed as '{meta.raw_value}' under label '{meta.source_label}': "
                                        f"confidence {meta.confidence:.2f}. {meta.warning or ''}".strip())
        elif getattr(inv, name) is not None and meta.confidence < 0.7:
            b.add(f"extraction:{name}", f"Field '{name}' = '{getattr(inv, name)}' has low confidence {meta.confidence:.2f}. {meta.warning or ''}".strip())
    for x in ("vendor_name", "invoice_number", "invoice_date", "total"):
        if getattr(inv, x) is None and not b.ids([f"extraction:{x}"]):
            b.add(f"extraction:{x}", f"Critical field '{x}' was not found on the document.")
    if not inv.line_items:
        b.add("extraction:line_items", "No line items could be extracted from the document.")

    v = f.vendor
    b.add("vendor", f"Vendor match: {v.status}" + (f" -> {v.vendor_name} via {v.method} ({v.confidence:.0%})." if v.vendor_name else ". ")
          + " ".join(v.notes))
    bk = f.bank
    b.add("bank", f"Bank check: {bk.status}. Invoice account {bk.invoice_account_masked or 'n/a'}; registered "
                  f"{', '.join(bk.registered_masked) or 'none'}. {bk.note}")
    po = f.po
    b.add("po", f"PO match: {po.status}" + (f" -> {po.po_number}" if po.po_number else "") + f". {po.note}")

    if receipts:
        b.add("receipts", "Goods receipts on this PO: " + "; ".join(
            f"{r['grn']} on {r['date']} ({', '.join(f'{i['sku']} x{i['qty']}' for i in r['items'])})" for r in receipts) + ".")
    elif f.recon is not None:
        b.add("receipts", "No goods receipts are recorded against this PO.")
    if history:
        b.add("history", "Earlier invoices on this PO (non-rejected): " + "; ".join(
            f"{h['invoice_number']} ({h['status']}, {', '.join(f'{k} x{v}' for k, v in h['qty'].items())})" for h in history) + ".")
    elif f.recon is not None:
        b.add("history", "No earlier invoices have been billed against this PO.")

    r = f.recon
    if r:
        for ln in r.lines:
            if ln.po_item_id is None:
                b.add(f"line:{ln.line_no}", f"Line {ln.line_no} ({ln.sku or ln.description}) does not map to any PO item.")
                continue
            b.add(f"line:{ln.line_no}",
                  f"Line {ln.line_no} {ln.sku}: PO qty {_q(ln.po_qty)}, previously invoiced {_q(ln.prior_invoiced_qty)}, this invoice "
                  f"{_q(ln.invoice_qty)}, cumulative {_q(ln.cumulative_invoiced_qty)}, received {_q(ln.received_qty)}; "
                  f"shortfall {_q(ln.receipt_shortfall)}, over-PO {_q(ln.qty_over_po)}; unit price {fmt_inr(ln.invoice_unit_price)} "
                  f"vs PO {fmt_inr(ln.po_unit_price)} ({ln.price_variance_pct if ln.price_variance_pct is not None else '—'}%).")
        b.add("totals", f"Invoice total {fmt_inr(r.invoice_total)} vs {fmt_inr(r.expected_total)} expected at PO prices "
                        f"(variance {r.total_variance_pct if r.total_variance_pct is not None else '—'}%).")
    a = f.arithmetic
    b.add("arith", "Invoice arithmetic: " + (" ".join(i.message for i in a.issues) if a.issues else f"{a.checks_run} checks passed."))
    d = f.duplicate
    if d.matches:
        t = d.matches[0]
        b.add("dup", f"Closest existing invoice: {t.invoice_number} (id {t.invoice_id}) similarity {d.top_score:.0%} - " + "; ".join(t.signals) + ".")
    else:
        b.add("dup", "No similar existing invoice found.")
    b.add("policy", f"Active policy: {policy_label}.")
    return b
