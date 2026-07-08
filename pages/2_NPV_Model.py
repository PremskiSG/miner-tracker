"""NPV model — editable assumptions per scenario, replicating the user's Excel
(accumulated discounted cashflow chart + NPV10 / EV/NPV10 / upside).

Layout mirrors the Excel: years as COLUMNS in the assumptions grid. The metal
price is a global input (applied to every model year), defaulting to the last
realized silver price extracted from the filings."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from miner_tracker import db, ui
from miner_tracker.npv import GlobalInputs, YearInputs, assumptions_from_inputs, compute
from miner_tracker.seeds import blank_scenario, seed_company_scenarios

st.set_page_config(page_title="NPV Model", layout="wide")
st.title("NPV Valuation Model")

company = ui.select_company()
if company is None:
    st.stop()

conn = db.connect()
seed_company_scenarios(conn, company["id"], company["ticker"])
scenarios = db.load_scenarios(conn, company["id"])

names = list(scenarios)
name = st.sidebar.selectbox("Scenario", names,
                            index=names.index("spot") if "spot" in names else 0)

with st.sidebar.expander("Duplicate scenario"):
    new_name = st.text_input("New scenario name", value=f"{name}-copy")
    if st.button("Duplicate"):
        db.save_scenario(conn, company["id"], new_name, scenarios[name])
        conn.commit()
        st.rerun()

assumptions = scenarios[name]
g_dict = dict(assumptions.get("globals", {}))
years_list = assumptions.get("years", []) or blank_scenario()["years"]
ui_prefs = dict(assumptions.get("ui", {}))

# ── latest realized silver price from the filings ─────────────────────────────
row = conn.execute(
    """SELECT period, value FROM quarterly_metrics
       WHERE company_id=? AND metric='silver_price_realized'
         AND value IS NOT NULL AND period LIKE '%-Q%'""",
    (company["id"],)).fetchall()
filing_price = filing_period = None
if row:
    latest = max(row, key=lambda r: float(r["period"].replace("-Q", ".")))
    filing_price, filing_period = round(latest["value"], 2), latest["period"]

# ── extend / truncate the model horizon ────────────────────────────────────────
last_year = st.sidebar.number_input("Last model year",
                                    value=int(years_list[-1]["year"]),
                                    min_value=int(years_list[0]["year"]), step=1)
while years_list[-1]["year"] < last_year:          # extend by copying last year
    nxt = dict(years_list[-1])
    nxt["year"] = years_list[-1]["year"] + 1
    years_list.append(nxt)
years_list = [y for y in years_list if y["year"] <= last_year]

st.subheader("Global inputs")
saved_price = float(years_list[0].get("price") or 0.0)
c = st.columns(7)
g_dict["payability"] = c[0].number_input(
    "Payability", value=float(g_dict.get("payability", 1.0)), step=0.01, format="%.2f")

track = c[1].checkbox("Track latest filing", value=bool(ui_prefs.get(
    "track_filing_price", False)) and filing_price is not None,
    disabled=filing_price is None,
    help="Apply the last realized silver price extracted from the filings "
         "to every model year")
price = c[1].number_input("Silver price (USD/oz)",
                          value=float(filing_price if track and filing_price
                                      else saved_price),
                          step=0.5, format="%.2f", disabled=track)
if track and filing_price is not None:
    price = filing_price
if filing_price is not None:
    c[1].caption(f"Last filing: ${filing_price:.2f} ({filing_period})")

g_dict["mining_tax"] = c[2].number_input(
    "Mining tax", value=float(g_dict.get("mining_tax", 0.0)), step=0.005, format="%.3f")
g_dict["discount_rate"] = c[3].number_input(
    "Discount rate", value=float(g_dict.get("discount_rate", 0.10)), step=0.01,
    format="%.2f")
mcap_m = c[4].number_input("Market cap (mUSD)",
                           value=float(g_dict.get("market_cap", 0.0)) / 1e6, step=1.0)
net_debt_m = c[5].number_input("Net debt (mUSD)",
                               value=float(g_dict.get("net_debt", 0.0)) / 1e6, step=1.0)
shares = c[6].number_input("Shares out (m)",
                           value=float(g_dict.get("shares_outstanding") or 0.0), step=1.0)
g_dict["market_cap"] = mcap_m * 1e6
g_dict["net_debt"] = net_debt_m * 1e6
g_dict["shares_outstanding"] = shares or None

st.subheader("Per-year assumptions")
ROW_FIELDS = ["production_oz", "aisc", "fx", "capex", "depreciation",
              "interest", "tax_rate"]
grid = (pd.DataFrame(years_list)
        .set_index("year")[ROW_FIELDS]
        .T)
grid.columns = [str(y) for y in grid.columns]
edited = st.data_editor(grid, use_container_width=True,
                        column_config={col: st.column_config.NumberColumn(width="small")
                                       for col in grid.columns})

g = GlobalInputs(**{k: v for k, v in g_dict.items()
                    if k in GlobalInputs.__dataclass_fields__})
years = []
for col in edited.columns:
    vals = {f: (float(edited.loc[f, col]) if pd.notna(edited.loc[f, col]) else 0.0)
            for f in ROW_FIELDS}
    years.append(YearInputs(year=int(col), price=float(price), **vals))
result = compute(g, years)

m = st.columns(5)
m[0].metric("NPV (10%)", f"${result.npv/1e6:,.1f}M")
m[1].metric("EV", f"${result.ev/1e6:,.1f}M")
m[2].metric("EV / NPV", f"{result.ev_npv:.2f}" if result.npv else "—")
m[3].metric("Upside vs MCap", f"{result.upside:+.1%}" if g.market_cap else "—")
m[4].metric("P/E (first year)", f"{result.years[0].pe:.1f}" if result.years else "—")

fig = go.Figure()
fig.add_bar(x=[y.year for y in result.years],
            y=[y.accumulated_dcf for y in result.years],
            name="Accumulated discounted cashflow", marker_color="#C55A11")
fig.add_scatter(x=[y.year for y in result.years],
                y=[result.ev] * len(result.years),
                name="Enterprise value", mode="lines",
                line=dict(color="#1F4E79", width=3))
fig.update_layout(title=f"{company['name']} NPV10 Cashflow (USD) — {name} "
                        f"@ ${price:.2f}/oz",
                  height=450, yaxis_tickformat="$,.0f")
st.plotly_chart(fig, use_container_width=True)

st.subheader("Computed per year")
comp = pd.DataFrame([{
    "revenue": y.revenue, "cost": y.cost, "net income": y.net_income,
    "discounted CF": y.discounted_cf, "accumulated DCF": y.accumulated_dcf,
    "EV/FCF": y.ev_fcf, "P/E": y.pe,
} for y in result.years], index=[str(y.year) for y in result.years]).T
st.dataframe(comp.style.format("{:,.1f}"), use_container_width=True)

if st.button("💾 Save scenario", type="primary"):
    out = assumptions_from_inputs(g, years)
    out["ui"] = {"track_filing_price": bool(track)}
    db.save_scenario(conn, company["id"], name, out)
    conn.commit()
    st.success(f"Saved scenario '{name}'.")
conn.close()
