import streamlit as st

from src.database.session import session_scope
from src.services.queries import invoice_rows
from src.ui.components import bootstrap, open_invoice

bootstrap("Invoice History", "🗂️")
st.title("Invoice history")
with session_scope() as s:
    df = invoice_rows(s)
c1, c2, c3 = st.columns([2, 2, 3])
status = c1.multiselect("Decision", sorted(df["decision"].unique()))
vendor = c2.multiselect("Vendor", sorted(df["vendor"].unique()))
q = c3.text_input("Search invoice no. / PO / file")
f = df
if status:
    f = f[f["decision"].isin(status)]
if vendor:
    f = f[f["vendor"].isin(vendor)]
if q:
    ql = q.lower()
    f = f[f.apply(lambda r: ql in f"{r['invoice_no']} {r['po']} {r['file']}".lower(), axis=1)]
st.caption(f"{len(f)} of {len(df)} invoices")
view = f[["id", "invoice_no", "vendor", "po", "invoice_date", "total", "decision", "overridden", "source", "reason"]]
ev = st.dataframe(view, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
                  column_config={"total": st.column_config.NumberColumn("total (₹)", format="₹%.2f"), "reason": st.column_config.TextColumn("main reason", width="large")})
sel = ev.selection.rows if ev and ev.selection else []
if sel:
    row = view.iloc[sel[0]]
    if st.button(f"Open invoice {row['invoice_no']} →", type="primary"):
        open_invoice(int(row["id"]))
else:
    st.caption("Select a row to open its full detail.")
