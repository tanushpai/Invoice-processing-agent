from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator

HEADER_FIELDS = [
    "vendor_name", "vendor_tax_id", "invoice_number", "invoice_date", "due_date",
    "service_start_date", "service_end_date", "po_number", "currency",
    "subtotal", "tax", "total", "payment_terms", "bank_account",
]
DATE_FIELDS = {"invoice_date", "due_date", "service_start_date", "service_end_date"}
MONEY_FIELDS = {"subtotal", "tax", "total"}
CRITICAL_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "total")


def _to_str(v):
    if v is None or isinstance(v, str):
        return v
    return str(v)


class ExtractedField(BaseModel):
    """One field as returned by the AI layer (always strings - Python parses numbers)."""
    value: str | None = None
    raw_value: str | None = None       # exactly as printed on the document
    confidence: float = Field(default=0.0, ge=0, le=1)
    source_label: str | None = None    # label printed on the invoice, e.g. "Begin Date"
    warning: str | None = None
    human_review_required: bool = False

    _coerce = field_validator("value", "raw_value", "source_label", "warning", mode="before")(_to_str)


class RawLineItem(BaseModel):
    description: str | None = None
    sku: str | None = None
    quantity: str | None = None
    unit_price: str | None = None
    tax_rate: str | None = None
    tax_amount: str | None = None
    line_total: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    warning: str | None = None

    _coerce = field_validator(
        "description", "sku", "quantity", "unit_price", "tax_rate", "tax_amount", "line_total", "warning",
        mode="before")(_to_str)


class RawInvoiceExtraction(BaseModel):
    fields: dict[str, ExtractedField]
    line_items: list[RawLineItem] = Field(default_factory=list)
    document_warnings: list[str] = Field(default_factory=list)


class FieldMeta(BaseModel):
    confidence: float = 0.0
    source_label: str | None = None
    raw_value: str | None = None
    warning: str | None = None
    human_review_required: bool = False


class NormalizedLineItem(BaseModel):
    line_no: int
    description: str | None = None
    sku: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    tax_rate: Decimal | None = None
    tax_amount: Decimal | None = None
    line_total: Decimal | None = None
    confidence: float = 0.5
    warning: str | None = None


class NormalizedInvoice(BaseModel):
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    invoice_number: str | None = None
    invoice_date: date | None = None
    due_date: date | None = None
    service_start_date: date | None = None
    service_end_date: date | None = None
    po_number: str | None = None
    currency: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    payment_terms: str | None = None
    bank_account: str | None = None
    line_items: list[NormalizedLineItem] = Field(default_factory=list)
    meta: dict[str, FieldMeta] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    def review_fields(self, min_conf: float) -> list[str]:
        """Fields a human must look at: explicitly flagged, or present-but-low-confidence."""
        out = []
        for name, m in self.meta.items():
            present = getattr(self, name) is not None
            if m.human_review_required or (present and m.confidence < min_conf):
                out.append(name)
        return out
