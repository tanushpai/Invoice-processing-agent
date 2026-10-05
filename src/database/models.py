from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, deferred, mapped_column, relationship

MONEY = Numeric(16, 2)
QTY = Numeric(14, 3)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Vendor(Base):
    __tablename__ = "vendors"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    normalized_name: Mapped[str] = mapped_column(String(200), index=True)
    tax_id: Mapped[str | None] = mapped_column(String(30), index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    payment_terms: Mapped[str | None] = mapped_column(String(50))
    category: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    bank_accounts: Mapped[list[VendorBankAccount]] = relationship(back_populates="vendor", cascade="all, delete-orphan")
    purchase_orders: Mapped[list[PurchaseOrder]] = relationship(back_populates="vendor")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="vendor")


class VendorBankAccount(Base):
    __tablename__ = "vendor_bank_accounts"
    id: Mapped[int] = mapped_column(primary_key=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    account_number: Mapped[str] = mapped_column(String(40), index=True)
    ifsc: Mapped[str | None] = mapped_column(String(20))
    bank_name: Mapped[str | None] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    vendor: Mapped[Vendor] = relationship(back_populates="bank_accounts")


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(40), unique=True)
    vendor_id: Mapped[int] = mapped_column(ForeignKey("vendors.id"), index=True)
    order_date: Mapped[date] = mapped_column(Date)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    status: Mapped[str] = mapped_column(String(20), default="open")
    description: Mapped[str | None] = mapped_column(String(300))

    vendor: Mapped[Vendor] = relationship(back_populates="purchase_orders")
    items: Mapped[list[PurchaseOrderItem]] = relationship(back_populates="po", cascade="all, delete-orphan",
                                                          order_by="PurchaseOrderItem.line_no")
    receipts: Mapped[list[GoodsReceipt]] = relationship(back_populates="po", cascade="all, delete-orphan")
    invoices: Mapped[list[Invoice]] = relationship(back_populates="po")


class PurchaseOrderItem(Base):
    __tablename__ = "purchase_order_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(60))
    description: Mapped[str] = mapped_column(String(300))
    quantity: Mapped[Decimal] = mapped_column(QTY)
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=Decimal("18"))
    po: Mapped[PurchaseOrder] = relationship(back_populates="items")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    id: Mapped[int] = mapped_column(primary_key=True)
    grn_number: Mapped[str] = mapped_column(String(40), unique=True)
    po_id: Mapped[int] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    received_date: Mapped[date] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(String(300))
    po: Mapped[PurchaseOrder] = relationship(back_populates="receipts")
    items: Mapped[list[GoodsReceiptItem]] = relationship(back_populates="receipt", cascade="all, delete-orphan")


class GoodsReceiptItem(Base):
    __tablename__ = "goods_receipt_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[int] = mapped_column(ForeignKey("goods_receipts.id"), index=True)
    po_item_id: Mapped[int] = mapped_column(ForeignKey("purchase_order_items.id"), index=True)
    quantity_received: Mapped[Decimal] = mapped_column(QTY)
    receipt: Mapped[GoodsReceipt] = relationship(back_populates="items")


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_number: Mapped[str | None] = mapped_column(String(80), index=True)
    vendor_id: Mapped[int | None] = mapped_column(ForeignKey("vendors.id"), index=True)
    vendor_name_raw: Mapped[str | None] = mapped_column(String(300))
    vendor_tax_id: Mapped[str | None] = mapped_column(String(30))
    invoice_date: Mapped[date | None] = mapped_column(Date)
    due_date: Mapped[date | None] = mapped_column(Date)
    service_start_date: Mapped[date | None] = mapped_column(Date)
    service_end_date: Mapped[date | None] = mapped_column(Date)
    po_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_orders.id"), index=True)
    po_number_raw: Mapped[str | None] = mapped_column(String(60))
    currency: Mapped[str | None] = mapped_column(String(3))
    subtotal: Mapped[Decimal | None] = mapped_column(MONEY)
    tax: Mapped[Decimal | None] = mapped_column(MONEY)
    total: Mapped[Decimal | None] = mapped_column(MONEY, index=True)
    payment_terms: Mapped[str | None] = mapped_column(String(80))
    bank_account: Mapped[str | None] = mapped_column(String(60))
    source_filename: Mapped[str | None] = mapped_column(String(255))
    file_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    extraction_method: Mapped[str | None] = mapped_column(String(20))
    extraction_provider: Mapped[str | None] = mapped_column(String(60))
    extraction_meta: Mapped[dict | None] = mapped_column(JSON)
    raw_text: Mapped[str | None] = mapped_column(Text)
    pdf_bytes: Mapped[bytes | None] = deferred(mapped_column(LargeBinary))
    status: Mapped[str] = mapped_column(String(30), default="PROCESSING", index=True)
    source: Mapped[str] = mapped_column(String(20), default="upload")
    duplicate_of_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    vendor: Mapped[Vendor | None] = relationship(back_populates="invoices")
    po: Mapped[PurchaseOrder | None] = relationship(back_populates="invoices")
    items: Mapped[list[InvoiceItem]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                    order_by="InvoiceItem.line_no")
    decision: Mapped[Decision | None] = relationship(back_populates="invoice", uselist=False,
                                                     cascade="all, delete-orphan")
    investigation: Mapped[Investigation | None] = relationship(back_populates="invoice", uselist=False,
                                                               cascade="all, delete-orphan")
    audit_events: Mapped[list[AuditEvent]] = relationship(back_populates="invoice", cascade="all, delete-orphan",
                                                          order_by="AuditEvent.id")


class InvoiceItem(Base):
    __tablename__ = "invoice_items"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    line_no: Mapped[int] = mapped_column(Integer)
    po_item_id: Mapped[int | None] = mapped_column(ForeignKey("purchase_order_items.id"), index=True)
    description: Mapped[str | None] = mapped_column(String(300))
    sku: Mapped[str | None] = mapped_column(String(60))
    quantity: Mapped[Decimal | None] = mapped_column(QTY)
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    tax_rate: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    line_total: Mapped[Decimal | None] = mapped_column(MONEY)
    invoice: Mapped[Invoice] = relationship(back_populates="items")


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    rules: Mapped[dict] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(String(300))
    created_by: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Investigation(Base):
    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), unique=True)
    provider: Mapped[str] = mapped_column(String(60))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str | None] = mapped_column(Text)
    root_cause: Mapped[str | None] = mapped_column(Text)
    risk_level: Mapped[str | None] = mapped_column(String(10))
    risk_rationale: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    suggested_decision: Mapped[str | None] = mapped_column(String(30))
    human_review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(4, 3))
    open_questions: Mapped[list | None] = mapped_column(JSON)
    warnings: Mapped[list | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invoice: Mapped[Invoice] = relationship(back_populates="investigation")
    findings: Mapped[list[InvestigationFinding]] = relationship(back_populates="investigation",
                                                                cascade="all, delete-orphan")


class InvestigationFinding(Base):
    __tablename__ = "investigation_findings"
    id: Mapped[int] = mapped_column(primary_key=True)
    investigation_id: Mapped[int] = mapped_column(ForeignKey("investigations.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    detail: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(10))
    evidence_refs: Mapped[list | None] = mapped_column(JSON)
    investigation: Mapped[Investigation] = relationship(back_populates="findings")


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), unique=True)
    decision: Mapped[str] = mapped_column(String(30), index=True)
    engine_decision: Mapped[str | None] = mapped_column(String(30))
    ai_suggested_decision: Mapped[str | None] = mapped_column(String(30))
    guard_notes: Mapped[list | None] = mapped_column(JSON)
    policy_id: Mapped[int | None] = mapped_column(ForeignKey("policies.id"))
    policy_version: Mapped[int | None] = mapped_column(Integer)
    reasons: Mapped[list | None] = mapped_column(JSON)
    recommended_action: Mapped[str | None] = mapped_column(Text)
    facts: Mapped[dict | None] = mapped_column(JSON)  # full evidence pack (matches, recon, policy results...)
    review_status: Mapped[str] = mapped_column(String(20), default="none", index=True)  # none|pending|resolved
    override_decision: Mapped[str | None] = mapped_column(String(30))
    reviewer: Mapped[str | None] = mapped_column(String(80))
    review_note: Mapped[str | None] = mapped_column(Text)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invoice: Mapped[Invoice] = relationship(back_populates="decision")

    @property
    def effective(self) -> str:
        return self.override_decision or self.decision


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(60))
    event_type: Mapped[str] = mapped_column(String(40))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    invoice: Mapped[Invoice] = relationship(back_populates="audit_events")
