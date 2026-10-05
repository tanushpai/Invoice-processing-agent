from pathlib import Path

import streamlit as st

from src.ai.factory import get_provider
from src.core.config import get_settings
from src.database import models as m
from src.database.session import get_sessionmaker
from src.services.sample_pdfs import sample_manifest
from src.ui.components import STAGE_ICON, badge, bootstrap, open_invoice
from src.ui.detail import render_decision_card
from src.workflow.graph import run_pipeline

bootstrap("Process Invoice", "📥")
st.title("Process an invoice")
settings = get_settings()
SAMPLES = Path(__file__).resolve().parents[1] / "data" / "sample_invoices"

opts = ["Auto (Gemini if API key is set)", "Offline rules engine (no AI calls)"] if settings.gemini_api_key else ["Offline rules engine (no API key configured)"]
engine_choice = st.radio("Extraction & investigation engine", opts, horizontal=True)
prefer = "offline" if "Offline" in engine_choice else "auto"
if not settings.gemini_api_key:
    st.caption("Running without Gemini: a conservative rule-based reader handles the sample invoice layouts. Add GEMINI_API_KEY to read arbitrary real-world invoices.")

pdf_bytes, filename = None, None
tab_s, tab_u = st.tabs(["🧪 Try a sample scenario", "⬆️ Upload your own PDF"])
with tab_s:
    man = sample_manifest()
    pick = st.selectbox("Scenario", man, format_func=lambda x: f"{x['title']}  →  expected {x['expected']}")
    st.info(pick["note"])
    st.caption("Tip: process the same sample twice to watch duplicate detection reject the second copy. Use *Demo tools* in the sidebar to reset.")
    if st.button("▶ Process sample", type="primary"):
        pdf_bytes, filename = (SAMPLES / pick["file"]).read_bytes(), pick["upload_name"]
with tab_u:
    up = st.file_uploader("Invoice PDF (text or scanned)", type=["pdf"])
    if up and st.button("▶ Process uploaded invoice", type="primary"):
        pdf_bytes, filename = up.getvalue(), up.name

if pdf_bytes is not None:
    provider = get_provider(settings, prefer)
    st.markdown(f"#### Processing `{filename}` with **{provider.name}**")
    box = st.status("Running workflow…", expanded=True)
    final = None
    Sm = get_sessionmaker()
    try:
        with Sm() as session:
            for kind, payload in run_pipeline(session, provider, settings, pdf_bytes, filename):
                if kind == "stage":
                    box.write(f"{STAGE_ICON[payload.status]} **{payload.label}** — {payload.detail}  \n<span class='small'>{payload.duration_ms} ms</span>",
                              unsafe_allow_html=True)
                else:
                    final = payload
    except Exception as e:  # database or unexpected failure while saving
        box.update(label="Processing failed", state="error")
        st.error(f"Could not complete processing: {type(e).__name__}: {e}")
        st.stop()
    dec = final["final_decision"].value
    box.update(label=f"Done — {dec.replace('_', ' ')}", state="complete" if final.get("invoice_id") else "error", expanded=False)
    iid = final["invoice_id"]
    with Sm() as session:
        inv = session.get(m.Invoice, iid)
        st.markdown(f"### Result {badge(dec)}", unsafe_allow_html=True)
        render_decision_card(inv)
    if st.button("Open full invoice detail →", type="primary"):
        open_invoice(iid)
