"""Reserves & resources by statement date.

Defaults to the company total (sum across all projects/pits, tonnage-weighted
grade); an asset selector narrows to specific pits/deposits and reveals the
per-project breakdown."""
import plotly.graph_objects as go
import streamlit as st

from miner_tracker import queries, ui

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

projects = sorted(df["project"].unique())
multi_project = len(projects) > 1
selected = projects
if multi_project:
    picked = st.sidebar.multiselect(
        "Assets / projects", projects, default=[],
        help="Leave empty for the company total (sum of all assets). Pick one or "
             "more to narrow the charts and show the per-asset breakdown.")
    selected = picked or projects
    df = df[df["project"].isin(selected)]

agg = queries.reserves_aggregate(df)

CATEGORY_ORDER = ["measured", "indicated", "measured_indicated", "inferred",
                  "proved", "probable", "pp"]
LABELS = {"measured": "Measured", "indicated": "Indicated",
          "measured_indicated": "M+I", "inferred": "Inferred",
          "proved": "Proved", "probable": "Probable", "pp": "P+P"}

scope = "all assets (company total)" if (not multi_project or set(selected) == set(projects)) \
    else ", ".join(selected)
st.caption(f"Showing: {scope}")

col1, col2 = st.columns(2)
fig_t = go.Figure()
fig_g = go.Figure()
for cat in CATEGORY_ORDER:
    sub = agg[agg["category"] == cat].sort_values("statement_date")
    if sub.empty:
        continue
    fig_t.add_bar(x=sub["statement_date"], y=sub["tonnage"], name=LABELS[cat])
    fig_g.add_scatter(x=sub["statement_date"], y=sub["grade_gpt"],
                      mode="lines+markers", name=LABELS[cat])
fig_t.update_layout(title="Tonnage by category", barmode="group", height=400)
fig_g.update_layout(title=f"{metal.capitalize()} grade (g/t, tonnage-weighted)",
                    height=400)
fig_t.update_xaxes(type="category")
fig_g.update_xaxes(type="category")
col1.plotly_chart(fig_t, use_container_width=True)
col2.plotly_chart(fig_g, use_container_width=True)

st.subheader("Aggregated by category")
st.dataframe(agg, use_container_width=True, hide_index=True)

if multi_project:
    st.subheader("Per-asset breakdown")
    st.dataframe(df, use_container_width=True, hide_index=True)
