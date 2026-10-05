"""Rule-based provider: zero-config demo mode (no API key needed) and deterministic test double.

It understands the synonym sets below via exact label matching. It is deliberately conservative:
ambiguous labels/dates are returned as null + human_review_required, never guessed. Real-world
messy invoices should use GeminiProvider; this exists so the app runs end-to-end without a key.
"""
from __future__ import annotations

import re

from src.agents.playbook import build_rule_based_report
from src.ai.provider import AIProvider
from src.core.dates import is_ambiguous_numeric_date, parse_date
from src.core.money import parse_decimal
from src.schemas.decision import Decision, InvestigationReport, PolicyResult
from src.schemas.invoice import ExtractedField, RawInvoiceExtraction, RawLineItem

LABELS: dict[str, list[str]] = {
    "vendor_name": ["supplier", "vendor", "seller", "billed from", "bill from", "sold by", "issued by"],
    "vendor_tax_id": ["gstin", "gst no", "gst number", "tax id", "vendor gstin"],
    "invoice_number": ["invoice no", "invoice number", "invoice #", "inv no", "inv #", "bill no", "bill number",
                       "tax invoice no"],
    "invoice_date": ["invoice date", "bill date", "dated", "date of issue", "date"],
    "due_date": ["due date", "payment due", "pay by", "due on"],
    "service_start_date": ["start date", "begin date", "starting date", "period from", "commencement date",
                           "service start", "from date"],
    "service_end_date": ["end date", "completion date", "period to", "ending date", "service end", "to date"],
    "po_number": ["po number", "po no", "po #", "purchase order", "purchase order no", "your order no",
                  "order ref", "po ref"],
    "currency": ["currency"],
    "subtotal": ["subtotal", "sub total", "taxable value", "net amount", "amount before tax"],
    "tax": ["total gst", "gst amount", "total tax", "tax amount", "gst", "igst", "vat"],
    "total": ["grand total", "total amount", "total payable", "amount due", "invoice total", "net payable", "total"],
    "payment_terms": ["payment terms", "terms", "terms of payment"],
    "bank_account": ["bank account", "account number", "account no", "a/c no", "a/c number", "beneficiary account"],
}
LABEL_INDEX = {syn: (field, i) for field, syns in LABELS.items() for i, syn in enumerate(syns)}
AMBIGUOUS_REF = {"ref", "ref no", "reference", "reference no", "reference number"}
_NUMTOK = re.compile(r"^\(?-?[\d,]+(\.\d+)?%?\)?$")
_SKU = re.compile(r"^[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)+$")


def _norm_label(s: str) -> str:
    s = re.sub(r"\(.*?\)", "", s.lower())
    return re.sub(r"\s+", " ", s).strip(" .")


class OfflineProvider(AIProvider):
    name = "offline-rules"
    is_llm = False

    def extract_invoice(self, text: str, pdf_bytes: bytes | None = None) -> RawInvoiceExtraction:
        fields: dict[str, ExtractedField] = {}
        ref_candidate: tuple[str, str] | None = None
        items: list[RawLineItem] = []
        in_table = False
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

        for line in lines:
            low = line.lower()
            if ("qty" in low or "quantity" in low) and ("price" in low or "rate" in low):
                in_table = True
                continue
            handled = False
            for seg in [s.strip() for s in line.split(" | ")]:
                if ":" not in seg:
                    continue
                label, val = seg.split(":", 1)
                nl, val = _norm_label(label), val.strip()
                if not val:
                    continue
                if nl in LABEL_INDEX:
                    field, idx = LABEL_INDEX[nl]
                    self._put(fields, field, val, label.strip(), 0.96 if idx == 0 else 0.9)
                    handled = True
                    in_table = False if field in ("subtotal", "tax", "total") else in_table
                elif nl in AMBIGUOUS_REF:
                    ref_candidate = (label.strip(), val)
                    handled = True
            if in_table and not handled:
                it = self._parse_row(line)
                if it:
                    items.append(it)

        if "invoice_number" not in fields and ref_candidate:
            lab, val = ref_candidate
            fields["invoice_number"] = ExtractedField(
                value=None, raw_value=val, source_label=lab, confidence=0.42, human_review_required=True,
                warning=f"Label '{lab}' could be an invoice number or a PO reference - not guessed")
        if "vendor_name" not in fields and lines:
            fields["vendor_name"] = ExtractedField(
                value=lines[0], raw_value=lines[0], confidence=0.6,
                warning="Vendor taken from the document header (no 'Supplier/Vendor' label)")
        for name in ("vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date", "service_start_date",
                     "service_end_date", "po_number", "currency", "subtotal", "tax", "total", "payment_terms",
                     "bank_account"):
            fields.setdefault(name, ExtractedField(value=None, confidence=0.0, warning="Not found on document"))
        warns = [] if items else ["No line items could be parsed from the document."]
        return RawInvoiceExtraction(fields=fields, line_items=items, document_warnings=warns)

    @staticmethod
    def _put(fields: dict, name: str, val: str, label: str, conf: float) -> None:
        if name in fields:
            return
        if name in ("invoice_date", "due_date", "service_start_date", "service_end_date"):
            d, amb = parse_date(val)
            if amb or is_ambiguous_numeric_date(val):
                fields[name] = ExtractedField(
                    value=None, raw_value=val, source_label=label, confidence=0.42, human_review_required=True,
                    warning=f"Date '{val}' found but its meaning is ambiguous (DD/MM vs MM/DD)")
            else:
                fields[name] = ExtractedField(value=d.isoformat() if d else None, raw_value=val,
                                              source_label=label, confidence=conf if d else 0.3,
                                              warning=None if d else f"Unrecognised date format '{val}'",
                                              human_review_required=d is None)
        elif name in ("subtotal", "tax", "total"):
            dec = parse_decimal(val)
            fields[name] = ExtractedField(value=str(dec) if dec is not None else None, raw_value=val,
                                          source_label=label, confidence=conf if dec is not None else 0.3,
                                          warning=None if dec is not None else f"'{val}' is not a number")
        else:
            fields[name] = ExtractedField(value=val, raw_value=val, source_label=label, confidence=conf)

    @staticmethod
    def _parse_row(line: str) -> RawLineItem | None:
        toks = [t for t in re.split(r"[|\s]+", line) if t]
        nums: list[str] = []
        while toks and _NUMTOK.match(toks[-1]) and len(nums) < 4:
            nums.insert(0, toks.pop())
        if len(nums) < 4 or not toks:
            return None
        sku = toks.pop() if len(toks) > 1 and _SKU.match(toks[-1]) else None
        qty, price, rate, total = nums
        return RawLineItem(description=" ".join(toks), sku=sku, quantity=qty.replace(",", ""),
                           unit_price=price.replace(",", ""), tax_rate=rate.replace("%", ""),
                           line_total=total.replace(",", ""), confidence=0.9)

    def investigate(self, facts: dict, evidence: dict[str, str]) -> InvestigationReport:
        results = [PolicyResult.model_validate(r) for r in facts.get("policy_results", [])]
        return build_rule_based_report(results, Decision(facts.get("engine_decision", "HUMAN_REVIEW")))
