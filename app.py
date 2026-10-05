import pandas as pd
import plotly.express as px
import streamlit as st

from src.core.money import fmt_inr
from src.database.session import session_scope
from src.services.queries import daily_counts, exception_reasons, invoice_rows
from src.ui.components import COLORS, bootstrap, open_invoice

bootstrap("Dashboard", "📊")
st.title("AP Investigator")
st.markdown("**It doesn't just extract invoices. It investigates whether an invoice should be paid, explains why, shows the evidence, "
            "applies company policy, and knows when to ask a human.**")

with session_scope() as s:
    df = invoice_rows(s)
    reasons = exception_reasons(s)
if df.empty:
    st.info("No invoices yet. Head to **Process Invoice**.")
    st.stop()

MIN_PER_INVOICE = 8  # ASSUMPTION: manual AP handling ~8 minutes per invoice
n = len(df)
auto = df[(df["decision"] == "APPROVE") & (~df["overridden"]) & (df["review_status"] == "none")]
blocked = df[df["decision"].isin(["REJECT", "HOLD", "CORRECTION_REQUIRED"])]
pending = df[df["review_status"] == "pending"]
c = st.columns(5)
c[0].metric("Invoices processed", n)
c[1].metric("Straight-through (auto-approved)", f"{len(auto) / n:.0%}")
c[2].metric("Awaiting human review", len(pending))
c[3].metric("Payments stopped / held", fmt_inr(blocked["total"].sum()), help="Value of invoices rejected, held or sent back for correction.")
c[4].metric("Est. AP time saved", f"{len(auto) * MIN_PER_INVOICE / 60:.1f} h", help=f"Assumption: {MIN_PER_INVOICE} min manual effort per auto-approved invoice.")

left, right = st.columns(2)
with left:
    counts = df["decision"].value_counts().reset_index()
    counts.columns = ["decision", "count"]
    fig = px.pie(counts, names="decision", values="count", hole=0.55, color="decision", color_discrete_map=COLORS, title="Decisions")
    fig.update_layout(margin=dict(t=50, b=10, l=10, r=10), height=340)
    st.plotly_chart(fig, width="stretch")
with right:
    if reasons:
        r = pd.DataFrame(reasons.most_common(8), columns=["rule", "count"])
        fig = px.bar(r, x="count", y="rule", orientation="h", title="Top exception reasons", color_discrete_sequence=["#2563eb"])
        fig.update_layout(margin=dict(t=50, b=10, l=10, r=10), height=340, yaxis=dict(autorange="reversed"))
        st.plotly_chart(fig, width="stretch")

dc = daily_counts(df)
if not dc.empty:
    fig = px.bar(dc, x="week", y="invoices", color="decision", color_discrete_map=COLORS, title="Invoices per week by decision")
    fig.update_layout(margin=dict(t=50, b=10, l=10, r=10), height=300)
    st.plotly_chart(fig, width="stretch")

vx = df[df["decision"] != "APPROVE"].groupby("vendor").size().sort_values(ascending=False).head(5)
if len(vx):
    st.markdown("**Vendors with most exceptions**")
    st.dataframe(vx.rename("exceptions").reset_index(), hide_index=True, width="stretch")

st.markdown("### Latest activity")
latest = df.head(8)
for _, row in latest.iterrows():
    a, b, cc, d = st.columns([2.2, 3, 1.4, 1])
    a.markdown(f"**{row['invoice_no']}**")
    b.write(row["vendor"])
    cc.markdown(f"`{row['decision']}`")
    if d.button("Open", key=f"open_{row['id']}"):
        open_invoice(int(row["id"]))
