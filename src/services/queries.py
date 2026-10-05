from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.database import models as m


def invoice_rows(session: Session) -> pd.DataFrame:
    rows = []
    for i in session.scalars(select(m.Invoice).order_by(m.Invoice.received_at.desc(), m.Invoice.id.desc())).all():
        d = i.decision
        rows.append({
            "id": i.id, "invoice_no": i.invoice_number or "(none)", "vendor": i.vendor.name if i.vendor else (i.vendor_name_raw or "(unknown)"),
            "vendor_id": i.vendor_id, "po": i.po.po_number if i.po else (i.po_number_raw or ""), "invoice_date": i.invoice_date,
            "total": float(i.total) if i.total is not None else None, "decision": d.effective if d else i.status,
            "system_decision": d.decision if d else i.status, "overridden": bool(d and d.override_decision),
            "review_status": d.review_status if d else "none", "source": i.source, "file": i.source_filename,
            "received_at": i.received_at, "reason": (d.reasons[0] if d and d.reasons else ""),
        })
    cols = ["id", "invoice_no", "vendor", "vendor_id", "po", "invoice_date", "total", "decision", "system_decision", "overridden",
            "review_status", "source", "file", "received_at", "reason"]
    return pd.DataFrame(rows, columns=cols)


def exception_reasons(session: Session) -> Counter:
    c: Counter = Counter()
    for d in session.scalars(select(m.Decision)).all():
        if d.decision == "APPROVE":
            continue
        for r in (d.reasons or []):
            if ":" in r:
                c[r.split(":")[0]] += 1
    return c


def vendor_table(session: Session) -> pd.DataFrame:
    rows = []
    for v in session.scalars(select(m.Vendor).order_by(m.Vendor.code)).all():
        invs = v.invoices
        ex = [i for i in invs if i.status != "APPROVE"]
        rows.append({"code": v.code, "vendor": v.name, "GSTIN": v.tax_id, "category": v.category, "terms": v.payment_terms,
                     "bank accounts": len(v.bank_accounts), "open POs": sum(p.status == "open" for p in v.purchase_orders),
                     "invoices": len(invs), "billed (₹)": float(sum((i.total or 0) for i in invs)),
                     "exception rate": (len(ex) / len(invs)) if invs else 0.0, "id": v.id})
    return pd.DataFrame(rows)


def po_progress(session: Session) -> pd.DataFrame:
    inv_q = dict(session.execute(
        select(m.PurchaseOrderItem.po_id, func.sum(m.InvoiceItem.quantity))
        .join(m.InvoiceItem, m.InvoiceItem.po_item_id == m.PurchaseOrderItem.id)
        .join(m.Invoice, m.Invoice.id == m.InvoiceItem.invoice_id).where(m.Invoice.status != "REJECT")
        .group_by(m.PurchaseOrderItem.po_id)).all())
    rec_q = dict(session.execute(
        select(m.PurchaseOrderItem.po_id, func.sum(m.GoodsReceiptItem.quantity_received))
        .join(m.GoodsReceiptItem, m.GoodsReceiptItem.po_item_id == m.PurchaseOrderItem.id)
        .group_by(m.PurchaseOrderItem.po_id)).all())
    rows = []
    for p in session.scalars(select(m.PurchaseOrder).order_by(m.PurchaseOrder.po_number)).all():
        ordered = sum(i.quantity for i in p.items)
        value = sum(i.quantity * i.unit_price * (1 + i.tax_rate / 100) for i in p.items)
        rec, invd = Decimal(rec_q.get(p.id) or 0), Decimal(inv_q.get(p.id) or 0)
        rows.append({"po": p.po_number, "vendor": p.vendor.name, "order date": p.order_date, "lines": len(p.items),
                     "ordered qty": float(ordered), "received qty": float(rec), "invoiced qty": float(invd),
                     "received %": float(rec / ordered * 100) if ordered else 0.0, "invoiced %": float(invd / ordered * 100) if ordered else 0.0,
                     "PO value (₹)": float(value), "id": p.id})
    return pd.DataFrame(rows)


def daily_counts(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    t = df.copy()
    t["week"] = pd.to_datetime(t["received_at"]).dt.to_period("W").dt.start_time
    return t.groupby(["week", "decision"]).size().reset_index(name="invoices")


__all__ = ["invoice_rows", "exception_reasons", "vendor_table", "po_progress", "daily_counts", "defaultdict"]
