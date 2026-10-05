import streamlit as st
from sqlalchemy import select

from src.core.money import fmt_inr
from src.database import models as m
from src.database.session import session_scope
from src.schemas.decision import Decision
from src.services.review import apply_override
from src.ui.components import bootstrap
from src.ui.detail import render_invoice_detail

bootstrap("Human Review", "🧑‍⚖️")
st.title("Human review queue")
st.caption("Cases the system refused to decide alone: ambiguous extraction, bank mismatch, duplicate suspicion, missing/multiple POs, high value.")
with session_scope() as s:
    pend = s.scalars(select(m.Invoice).join(m.Decision).where(m.Decision.review_status == "pending")
                     .order_by(m.Invoice.received_at)).all()
    st.metric("Pending", len(pend))
    if not pend:
        st.success("Queue is empty. 🎉")
    else:
        lab = {i.id: f"#{i.id} · {i.invoice_number or '(no number)'} · {i.vendor.name if i.vendor else i.vendor_name_raw} · {fmt_inr(i.total)} · {(i.decision.reasons or [''])[0].split(':')[0]}" for i in pend}
        pick = st.selectbox("Case", [i.id for i in pend], format_func=lambda i: lab[i])
        render_invoice_detail(s, pick, show_header=False)
        st.divider()
        st.markdown("### Record your decision")
        with st.form(f"review_{pick}"):
            a, b = st.columns([1, 1])
            reviewer = a.text_input("Your name", value=st.session_state.get("reviewer", ""))
            choice = b.selectbox("Decision", [d.value for d in Decision if d != Decision.HUMAN_REVIEW], format_func=lambda x: x.replace("_", " "))
            note = st.text_area("Note (mandatory, becomes part of the audit trail)")
            if st.form_submit_button("Submit decision", type="primary"):
                try:
                    st.session_state["reviewer"] = reviewer
                    apply_override(s, pick, reviewer, Decision(choice), note)
                    st.success("Decision recorded and audited.")
                    st.rerun()
                except ValueError as e:
                    st.error(str(e))
    st.markdown("### Recently resolved")
    done = s.scalars(select(m.Decision).where(m.Decision.review_status == "resolved").order_by(m.Decision.reviewed_at.desc()).limit(15)).all()
    st.dataframe([{"invoice": d.invoice.invoice_number, "system said": d.decision, "reviewer decided": d.override_decision, "by": d.reviewer,
                   "when": d.reviewed_at, "note": d.review_note} for d in done], hide_index=True, width="stretch")
