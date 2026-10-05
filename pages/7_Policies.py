import pandas as pd
import streamlit as st
from sqlalchemy import select

from src.database import models as m
from src.database.session import session_scope
from src.policies.engine import DEFAULT_RULES, RULE_DOCS, get_active_policy, publish_new_version
from src.ui.components import bootstrap

bootstrap("Policies", "📜")
st.title("Business policy")
st.caption("Deterministic and versioned. Every decision records the policy version it was made under. AI can never relax these rules.")
with session_scope() as s:
    pol = get_active_policy(s)
    st.markdown(f"### Active: {pol.name} · **v{pol.version}**")
    with st.form("policy_form"):
        new, cols = {}, st.columns(2)
        for i, (k, default) in enumerate(DEFAULT_RULES.items()):
            cur = pol.rules.get(k, default)
            with cols[i % 2]:
                if k.endswith("_action"):
                    opts = ["HOLD", "HUMAN_REVIEW", "CORRECTION_REQUIRED", "REJECT"]
                    new[k] = st.selectbox(k, opts, index=opts.index(cur) if cur in opts else 1, help=RULE_DOCS[k])
                elif isinstance(default, int):
                    new[k] = st.number_input(k, value=int(cur), step=1, help=RULE_DOCS[k])
                else:
                    new[k] = st.number_input(k, value=float(cur), step=0.01 if float(default) <= 1 else 1.0, format="%.2f", help=RULE_DOCS[k])
        author = st.text_input("Your name")
        notes = st.text_input("Change note")
        if st.form_submit_button("Publish as new version", type="primary"):
            changed = {k: (pol.rules.get(k), v) for k, v in new.items() if pol.rules.get(k) != v}
            if not changed:
                st.warning("Nothing changed.")
            elif not author.strip() or not notes.strip():
                st.error("Name and change note are required.")
            else:
                p = publish_new_version(s, new, f"{notes} (changed: {', '.join(changed)})", author.strip())
                s.commit()
                st.success(f"Published v{p.version}. New invoices will use it; past decisions keep their original version.")
                st.rerun()
    st.markdown("### Version history")
    vs = s.scalars(select(m.Policy).order_by(m.Policy.version.desc())).all()
    st.dataframe(pd.DataFrame([{"version": f"v{p.version}", "active": "●" if p.is_active else "", "created": p.created_at, "by": p.created_by, "notes": p.notes} for p in vs]),
                 hide_index=True, width="stretch")
    with st.expander("How decisions are combined"):
        st.markdown("Every rule is evaluated independently. The **most restrictive** failed rule wins: `REJECT > HUMAN_REVIEW > CORRECTION_REQUIRED > HOLD > APPROVE`. "
                    "The AI investigator may *escalate* (e.g. APPROVE→HUMAN_REVIEW) but a deterministic guard overrides any attempt to relax a decision, "
                    "and AI can never reject on its own.")
