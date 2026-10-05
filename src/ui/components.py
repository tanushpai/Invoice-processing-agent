from __future__ import annotations

import streamlit as st

from src.core.config import get_settings, load_streamlit_secrets_into_env

COLORS = {"APPROVE": "#16a34a", "HOLD": "#d97706", "CORRECTION_REQUIRED": "#ea580c", "HUMAN_REVIEW": "#2563eb", "REJECT": "#dc2626",
          "PROCESSING": "#64748b"}
ICON = {"APPROVE": "✅", "HOLD": "⏸️", "CORRECTION_REQUIRED": "✏️", "HUMAN_REVIEW": "🧑‍⚖️", "REJECT": "⛔"}
STAGE_ICON = {"ok": "✅", "warn": "⚠️", "skipped": "⏭️", "error": "❌"}
RULE_ICON = {"pass": "✅", "fail": "❌", "warn": "⚠️", "skipped": "⏭️"}

CSS = """
<style>
.block-container {padding-top: 2.2rem; max-width: 1280px;}
.badge {display:inline-block; padding:3px 12px; border-radius:999px; color:#fff; font-weight:600; font-size:0.85rem; letter-spacing:.02em;}
.decision-hero {border-radius:14px; padding:18px 22px; color:#fff; margin-bottom:12px;}
.decision-hero h2 {margin:0; color:#fff; font-size:1.7rem;}
.decision-hero p {margin:4px 0 0 0; opacity:.92;}
.evid {border-left:4px solid #94a3b8; background:rgba(148,163,184,.12); padding:10px 14px; border-radius:6px; margin:8px 0;}
.evid.fail {border-left-color:#dc2626;} .evid.warn {border-left-color:#d97706;}
.evid b {font-size:.95rem;}
.chip {display:inline-block; background:rgba(100,116,139,.2); border-radius:6px; padding:1px 7px; margin-right:4px; font-size:.75rem; font-family:monospace;}
.tl {border-left:2px solid #cbd5e1; margin-left:8px; padding-left:16px; padding-bottom:10px;}
.small {font-size:.82rem; opacity:.8;}
</style>
"""


def badge(decision: str) -> str:
    c = COLORS.get(decision, "#64748b")
    return f'<span class="badge" style="background:{c}">{ICON.get(decision, "")} {decision.replace("_", " ")}</span>'


def bootstrap(title: str, icon: str = "🧾"):
    st.set_page_config(page_title=f"{title} · AP Investigator", page_icon=icon, layout="wide")
    load_streamlit_secrets_into_env()
    st.markdown(CSS, unsafe_allow_html=True)
    _init_once()
    sidebar()


@st.cache_resource(show_spinner="Preparing database…")
def _init_once() -> bool:
    from src.database.seed import ensure_seeded

    return ensure_seeded()


def sidebar() -> None:
    s = get_settings()
    with st.sidebar:
        st.markdown("### 🧾 AP Investigator")
        st.caption("AI investigates. Python decides.")
        ai = "Gemini " + s.gemini_model if s.gemini_api_key else "Offline rules (no GEMINI_API_KEY)"
        st.markdown(f"**AI engine:** {ai}")
        st.markdown(f"**Database:** {'PostgreSQL (Supabase)' if s.is_postgres else 'SQLite (local demo)'}")
        with st.expander("Demo tools"):
            st.caption("Wipes all data and re-seeds 10 vendors, 15 POs, 25 receipts, 30 invoices.")
            if st.checkbox("I understand this deletes processed invoices", key="reset_ok") and st.button("Reset demo data"):
                from src.database.seed import reset_demo_data

                st.success(f"Re-seeded: {reset_demo_data()}")


def open_invoice(invoice_id: int) -> None:
    st.session_state["detail_id"] = int(invoice_id)
    st.switch_page("pages/3_Invoice_Detail.py")


def decision_hero(decision: str, policy: str, subtitle: str = "") -> None:
    c = COLORS.get(decision, "#64748b")
    st.markdown(f'<div class="decision-hero" style="background:{c}"><h2>{ICON.get(decision, "")} {decision.replace("_", " ")}</h2>'
                f'<p>{subtitle}</p><p class="small" style="color:#fff">{policy}</p></div>', unsafe_allow_html=True)


def evidence_block(result: dict, catalog: dict[str, str]) -> None:
    cls = "fail" if result["status"] == "fail" else "warn"
    rows = "".join(f"<tr><td style='padding-right:18px;opacity:.75'>{k}</td><td><b>{v}</b></td></tr>" for k, v in result.get("evidence", []))
    chips = "".join(f'<span class="chip" title="{catalog.get(i, "")}">{i}</span>' for i in result.get("evidence_ids", []))
    impact = f" → <b>{result['impact'].replace('_', ' ')}</b>" if result.get("impact") else ""
    st.markdown(f'<div class="evid {cls}"><b>{RULE_ICON[result["status"]]} {result["name"]}</b>{impact}<br>'
                f'<span class="small">Policy: {result.get("threshold", "")}</span><br>{result["detail"]}'
                f'<table style="margin-top:6px">{rows}</table><div style="margin-top:4px">{chips}</div></div>', unsafe_allow_html=True)
