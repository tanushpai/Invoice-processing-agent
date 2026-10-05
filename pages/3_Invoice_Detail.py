import streamlit as st

from src.database.session import session_scope
from src.services.queries import invoice_rows
from src.ui.components import bootstrap
from src.ui.detail import render_invoice_detail

bootstrap("Invoice Detail", "🔍")
with session_scope() as s:
    df = invoice_rows(s)
    ids = df["id"].tolist()
    labels = {int(r.id): f"#{r.id} · {r.invoice_no} · {r.vendor} · {r.decision}" for r in df.itertuples()}
    cur = st.session_state.get("detail_id", ids[0] if ids else None)
    if cur not in ids:
        cur = ids[0] if ids else None
    if cur is None:
        st.info("No invoices yet.")
        st.stop()
    chosen = st.selectbox("Invoice", ids, index=ids.index(cur), format_func=lambda i: labels[int(i)])
    st.session_state["detail_id"] = int(chosen)
    render_invoice_detail(s, int(chosen))
