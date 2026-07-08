"""Reserves & resources by statement date."""
import plotly.graph_objects as go
import streamlit as st

from miner_tracker import ui

st.set_page_config(page_title="Reserves", layout="wide")
st.title("Reserves & Resources")

company = ui.select_company()
if company is None:
    st.stop()

df = ui.reserves(company["id"])
if df.empty:
    st.info("No reserves statements extracted yet (they come from annual reports).")
    st.stop()

metals = sorted(df["metal"].unique())
default_metal = metals.index("silver") if "silver" in metals else 0
metal = st.sidebar.selectbox("Metal", metals, index=default_metal)
df = df[df["metal"] == metal]

CATEGORY_ORDER = ["measured", "indicated", "measured_indicated", "inferred", "pp"]
LABELS = {"measured": "Measured", "indicated": "Indicated",
          "measured_indicated": "M+I", "inferred": "Inferred", "pp": "P+P"}

col1, col2 = st.columns(2)
fig_t = go.Figure()
fig_g = go.Figure()
for cat in CATEGORY_ORDER:
    sub = df[df["category"] == cat].sort_values("statement_date")
    if sub.empty:
        continue
    fig_t.add_bar(x=sub["statement_date"], y=sub["tonnage"], name=LABELS[cat])
    fig_g.add_scatter(x=sub["statement_date"], y=sub["grade_gpt"],
                      mode="lines+markers", name=LABELS[cat])
fig_t.update_layout(title="Tonnage by category", barmode="group", height=400)
fig_g.update_layout(title=f"{metal.capitalize()} grade (g/t)", height=400)
fig_t.update_xaxes(type="category")
fig_g.update_xaxes(type="category")
col1.plotly_chart(fig_t, use_container_width=True)
col2.plotly_chart(fig_g, use_container_width=True)

st.subheader("Raw statements")
st.dataframe(df, use_container_width=True, hide_index=True)
