from decimal import Decimal

import pandas as pd
import streamlit as st
from sqlalchemy import func, select

from src.core.money import fmt_inr, fmt_qty
from src.database import models as m
from src.database.session import session_scope
from src.services.queries import po_progress
from src.ui.components import bootstrap

bootstrap("Purchase Orders", "📦")
st.title("Purchase orders & fulfilment")
st.caption("Ordered vs received vs invoiced - the raw material of the three-way match and of split-invoice tracking.")
with session_scope() as s:
    pt = po_progress(s)
    ev = st.dataframe(pt.drop(columns=["id"]), hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      column_config={"received %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
                                     "invoiced %": st.column_config.ProgressColumn(min_value=0, max_value=100, format="%.0f%%"),
                                     "PO value (₹)": st.column_config.NumberColumn(format="₹%.0f")})
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        po = s.get(m.PurchaseOrder, int(pt.iloc[sel[0]]["id"]))
        st.markdown(f"### {po.po_number} · {po.vendor.name}")
        rec = dict(s.execute(select(m.GoodsReceiptItem.po_item_id, func.sum(m.GoodsReceiptItem.quantity_received)).group_by(m.GoodsReceiptItem.po_item_id)).all())
        inv = dict(s.execute(select(m.InvoiceItem.po_item_id, func.sum(m.InvoiceItem.quantity)).join(m.Invoice).where(m.Invoice.status != "REJECT")
                             .group_by(m.InvoiceItem.po_item_id)).all())
        st.dataframe(pd.DataFrame([{"line": i.line_no, "sku": i.sku, "description": i.description, "ordered": fmt_qty(i.quantity),
                                    "received": fmt_qty(Decimal(rec.get(i.id) or 0)), "invoiced": fmt_qty(Decimal(inv.get(i.id) or 0)),
                                    "unit price": fmt_inr(i.unit_price), "GST %": str(i.tax_rate.normalize())} for i in po.items]),
                     hide_index=True, width="stretch")
        a, b = st.columns(2)
        with a:
            st.markdown("**Goods receipts**")
            for g in po.receipts:
                st.write(f"{g.grn_number} · {g.received_date} · " + ", ".join(f"{next(x.sku for x in po.items if x.id == gi.po_item_id)} × {fmt_qty(gi.quantity_received)}" for gi in g.items))
            if not po.receipts:
                st.caption("None yet.")
        with b:
            st.markdown("**Invoices against this PO**")
            for i in po.invoices:
                st.write(f"{i.invoice_number} · {fmt_inr(i.total)} · {i.status}")
            if not po.invoices:
                st.caption("None yet.")
