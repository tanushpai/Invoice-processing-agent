import pandas as pd
import streamlit as st

from src.core.money import mask_account
from src.database import models as m
from src.database.session import session_scope
from src.services.queries import vendor_table
from src.ui.components import bootstrap, open_invoice

bootstrap("Vendors", "🏢")
st.title("Vendor master")
with session_scope() as s:
    vt = vendor_table(s)
    ev = st.dataframe(vt.drop(columns=["id"]), hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                      column_config={"billed (₹)": st.column_config.NumberColumn(format="₹%.0f"),
                                     "exception rate": st.column_config.ProgressColumn(min_value=0, max_value=1, format="%.0f%%")})
    sel = ev.selection.rows if ev and ev.selection else []
    if sel:
        v = s.get(m.Vendor, int(vt.iloc[sel[0]]["id"]))
        st.markdown(f"### {v.name}")
        st.caption(f"GSTIN {v.tax_id} · {v.payment_terms} · {v.category}")
        st.markdown("**Registered bank accounts** (invoices must match one of these)")
        st.dataframe(pd.DataFrame([{"account": mask_account(b.account_number), "IFSC": b.ifsc, "bank": b.bank_name, "active": b.is_active} for b in v.bank_accounts]),
                     hide_index=True, width="stretch")
        st.markdown("**Invoices**")
        for i in sorted(v.invoices, key=lambda x: x.received_at, reverse=True):
            c1, c2, c3 = st.columns([3, 2, 1])
            c1.write(i.invoice_number)
            c2.write(i.status)
            if c3.button("Open", key=f"v{i.id}"):
                open_invoice(i.id)
    else:
        st.caption("Select a vendor to see bank accounts and invoices.")
