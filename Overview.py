"""Company Overview — quarterly metric trend charts from extracted filings."""
import plotly.graph_objects as go
import streamlit as st

from miner_tracker import ui

st.set_page_config(page_title="Miner Tracker", layout="wide")
st.title("Company Overview")

company = ui.select_company()
if company is None:
    st.stop()

long = ui.metrics_long(company["id"])
if long.empty:
    st.info("No extracted data yet for this company. Run "
            "`python -m miner_tracker extract` after setting your Anthropic API key.")
    st.stop()

usd = st.sidebar.toggle("Show in USD", value=False,
                        help="Converts reported-currency values with quarterly "
                             "average FX (run `python -m miner_tracker fx` to refresh)")
value_col = "value_usd" if usd else "value"

available = sorted(long["metric"].unique())
default = [m for m in ["revenue", "ebitda", "net_income", "silver_production_oz",
                       "gold_production_oz", "aisc_reported", "aisc_derived",
                       "head_grade_gpt", "ore_milled_t", "tpd_derived"]
           if m in available]
selected = st.multiselect("Metrics", available, default=default)

long = long.sort_values("period", key=ui.period_sort_key)
cols = st.columns(2)
for i, metric in enumerate(selected):
    sub = long[long["metric"] == metric].dropna(subset=[value_col])
    if sub.empty:
        continue
    fig = go.Figure(go.Bar(x=sub["period"], y=sub[value_col],
                           marker_color="#C55A11"))
    ccy = ("USD" if usd else (sub["currency"].dropna().iloc[0]
                              if sub["currency"].notna().any() else ""))
    unit = "" if ccy else (sub["unit"].dropna().iloc[0]
                           if sub["unit"].notna().any() else "")
    label = ccy or unit
    fig.update_layout(title=f"{metric}{f' ({label})' if label else ''}",
                      height=320, margin=dict(l=10, r=10, t=40, b=10))
    cols[i % 2].plotly_chart(fig, use_container_width=True)

st.subheader("Quarterly table")
wide = long.pivot_table(index="period", columns="metric", values=value_col,
                        aggfunc="first")
wide = wide.loc[sorted(wide.index, key=lambda p: float(p.replace("-Q", ".")))]
conf = long.pivot_table(index="period", columns="metric", values="confidence",
                        aggfunc="first").reindex(wide.index)


def _highlight(_df):
    return conf.reindex(columns=wide.columns).map(
        lambda c: "background-color: #fff3cd" if c in ("low", "medium")
        else ("background-color: #f8d7da" if c == "manual" else ""))


st.dataframe(wide.style.apply(_highlight, axis=None).format("{:,.2f}"),
             use_container_width=True)
st.caption("Yellow = low/medium extraction confidence · red = manual override. "
           "Review them on the Extraction Review page.")
