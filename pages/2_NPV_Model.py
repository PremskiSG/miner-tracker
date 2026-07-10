"""NPV model — editable assumptions per scenario, replicating the user's Excel
(accumulated discounted cashflow chart + NPV10 / EV/NPV10 / upside).

Layout mirrors the Excel: years as COLUMNS in the assumptions grid. The metal
price is a global input (applied to every model year), defaulting to the last
realized silver price extracted from the filings."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from miner_tracker import db, queries, ui
from miner_tracker.npv import (GlobalInputs, YearInputs, assumptions_from_inputs,
                               carry_forward_aisc, compute, mine_life)
from miner_tracker.seeds import blank_scenario, seed_company_scenarios

st.set_page_config(page_title="NPV Model", layout="wide")
st.title("NPV Valuation Model")

company = ui.select_company()
if company is None:
    st.stop()

metal = (company.get("metal") or "silver").lower()
conn = db.connect()
seed_company_scenarios(conn, company["id"], company["ticker"], metal=metal)
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

# ── latest realized metal price from the filings (converted to USD) ───────────
row = conn.execute(
    f"""SELECT m.period, m.value, m.currency, f.rate
       FROM quarterly_metrics m
       LEFT JOIN fx_rates f ON f.pair = m.currency || 'USD' AND f.period = m.period
       WHERE m.company_id=? AND m.metric='{metal}_price_realized'
         AND m.value IS NOT NULL AND m.period LIKE '%-Q%'""",
    (company["id"],)).fetchall()
filing_price = filing_period = None
if row:
    latest = max(row, key=lambda r: float(r["period"].replace("-Q", ".")))
    value = latest["value"]
    if (latest["currency"] or "USD") != "USD":
        value = value * latest["rate"] if latest["rate"] else None
    if value is not None:
        filing_price, filing_period = round(value, 2), latest["period"]

# ── extend / truncate the model horizon ────────────────────────────────────────
last_year = st.sidebar.number_input("Last model year",
                                    value=int(years_list[-1]["year"]),
                                    min_value=int(years_list[0]["year"]), step=1)
while years_list[-1]["year"] < last_year:          # extend by copying last year
    nxt = dict(years_list[-1])
    nxt["year"] = years_list[-1]["year"] + 1
    years_list.append(nxt)
years_list = [y for y in years_list if y["year"] <= last_year]

stats = queries.filings_stats(conn, company["id"])

st.subheader("Global inputs")
saved_price = float(years_list[0].get("price") or 0.0)
c = st.columns(7)
g_dict["payability"] = c[0].number_input(
    "Payability", value=float(g_dict.get("payability", 1.0)), step=0.01, format="%.2f")
if stats and stats["pay_avg"]:
    c[0].caption(f"Filings: min {stats['pay_min']:.2f} · avg {stats['pay_avg']:.2f} "
                 f"· max {stats['pay_max']:.2f}")

track = c[1].checkbox("Track latest filing", value=bool(ui_prefs.get(
    "track_filing_price", False)) and filing_price is not None,
    disabled=filing_price is None,
    help="Apply the last realized silver price extracted from the filings "
         "to every model year")
price = c[1].number_input(f"{metal.capitalize()} price (USD/oz)",
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


def _apply_to_all_years(field: str, value: float) -> None:
    """Persist a filings-derived value into every model year of this scenario."""
    s = scenarios[name]
    for y in s.get("years", []):
        y[field] = round(value, 2)
    db.save_scenario(conn, company["id"], name, s)
    conn.commit()
    st.rerun()


if stats and (stats["aisc_usd"] or stats["interest_usd"]):
    fc = st.columns([3, 1.2, 1.4])
    parts = []
    if stats["aisc_usd"]:
        how = ("as reported by the company" if stats.get("aisc_source") == "reported"
               else "= (opex + capex) / oz, the Excel back-calc")
        parts.append(f"AISC ${stats['aisc_usd']:,.2f}/oz ({how})")
    if stats["interest_usd"]:
        parts.append(f"interest ${stats['interest_usd']/1e6:.2f}M/yr")
    fc[0].caption("From filings, trailing 4 quarters: " + " · ".join(parts))
    if stats["aisc_usd"] and fc[1].button("Apply AISC to all years"):
        _apply_to_all_years("aisc", stats["aisc_usd"])
    if stats["interest_usd"] and fc[2].button("Apply interest to all years"):
        _apply_to_all_years("interest", stats["interest_usd"])

ROW_FIELDS = ["production_oz", "aisc", "fx", "capex", "depreciation",
              "interest", "tax_rate"]
grid = (pd.DataFrame(years_list)
        .set_index("year")[ROW_FIELDS]
        .T)
grid.columns = [str(y) for y in grid.columns]
edited = st.data_editor(grid, use_container_width=True,
                        column_config={col: st.column_config.NumberColumn(width="small")
                                       for col in grid.columns})
st.caption("Edit production and (optionally) AISC per year above — this grid IS the "
           "production forecast. A blank AISC year inherits the previous year's.")

g = GlobalInputs(**{k: v for k, v in g_dict.items()
                    if k in GlobalInputs.__dataclass_fields__})
years = []
for col in edited.columns:
    vals = {f: (float(edited.loc[f, col]) if pd.notna(edited.loc[f, col]) else 0.0)
            for f in ROW_FIELDS}
    years.append(YearInputs(year=int(col), price=float(price), **vals))
carry_forward_aisc(years)   # blank AISC year -> prior year's AISC
result = compute(g, years)

m = st.columns(5)
m[0].metric("NPV (10%)", f"${result.npv/1e6:,.1f}M")
m[1].metric("EV", f"${result.ev/1e6:,.1f}M")
m[2].metric("EV / NPV", f"{result.ev_npv:.2f}" if result.npv else "—")
m[3].metric("Upside vs MCap", f"{result.upside:+.1%}" if g.market_cap else "—")
m[4].metric("P/E (first year)", f"{result.years[0].pe:.1f}" if result.years else "—")

# ── Mine life: reserves outstanding vs the production forecast above ──────────
st.subheader("Mine life")
mo = queries.mineable_ounces(conn, company["id"])  # reserve_oz + resource split
if mo is None:
    st.info(f"No reserves/resources on file for {company['name']} — add them on the "
            "Reserves page (from an annual report) to estimate mine life.")
else:
    sc = st.columns(2)
    factor = sc[0].slider("M&I resource conversion factor", 0.0, 1.0, 0.60, 0.05,
                          help="Share of Measured+Indicated resources counted as "
                               "mineable. Reserves (Proved+Probable) always count 100%.")
    inf_factor = sc[1].slider("Inferred conversion factor", 0.0, 1.0, 0.0, 0.05,
                              help="Share of Inferred resources counted as mineable. "
                                   "Default 0 (excluded). Raise it for resource-stage "
                                   "producers whose mine plan draws on Inferred material.")
    reserve_oz = mo["reserve_oz"]
    resource_oz = mo["resource_mi_oz"]
    inferred_oz = mo.get("inferred_oz", 0.0)
    mineable = reserve_oz + factor * resource_oz + inf_factor * inferred_oz
    stmt_year = int(mo["statement_date"][:4])
    ml = mine_life(mineable, years, from_year=stmt_year)
    forecast_oz = sum(y.production_oz or 0 for y in years if y.year >= stmt_year)

    if forecast_oz <= 0:
        life_str, depl_str = "— yrs", "enter a production forecast above"
    elif ml.depletion_year:
        life_str = f"{ml.years:.1f} yrs"
        depl_str = f"depleted ~{ml.depletion_year}"
    else:
        life_str = f"≥ {ml.years:.0f} yrs"
        depl_str = f"{ml.remaining_oz/1e3:,.0f} koz left after {years[-1].year}"
    mm = st.columns(5)
    mm[0].metric(f"Mine life ({mo['metal']})", life_str, depl_str, delta_color="off")
    mm[1].metric("Mineable oz", f"{mineable/1e3:,.0f} koz")
    mm[2].metric("Reserves (P&P)", f"{reserve_oz/1e3:,.0f} koz")
    mm[3].metric(f"{factor:.0%} of M&I resource",
                 f"{factor*resource_oz/1e3:,.0f} koz",
                 f"of {resource_oz/1e3:,.0f} koz M&I", delta_color="off")
    mm[4].metric(f"{inf_factor:.0%} of Inferred",
                 f"{inf_factor*inferred_oz/1e3:,.0f} koz",
                 f"of {inferred_oz/1e3:,.0f} koz Inf", delta_color="off")
    caption = (f"Basis: {mo['basis']} as at {mo['statement_date']} · reserves + "
               f"{factor:.0%} of M&I resources, walked against the production "
               f"forecast from {stmt_year}. ")
    if forecast_oz <= 0:
        caption += "Set production per year in the grid above to compute mine life."
    elif ml.depletion_year:
        caption += f"At the planned rate, reserves support production to ~{ml.depletion_year}."
    else:
        caption += "Reserves outlast the forecast horizon."
    if reserve_oz and resource_oz:
        caption += (" Note: if the company reports M&I *inclusive* of reserves, this "
                    "additive basis overstates — lower the factor to compensate.")
    st.caption(caption)

fig = go.Figure()
fig.add_bar(x=[y.year for y in result.years],
            y=[y.accumulated_dcf for y in result.years],
            name="Accumulated discounted cashflow", marker_color="#C55A11")
fig.add_scatter(x=[y.year for y in result.years],
                y=[result.ev] * len(result.years),
                name="Enterprise value", mode="lines",
                line=dict(color="#1F4E79", width=3))
fig.update_layout(title=f"{company['name']} NPV10 Cashflow (USD) — {name} "
                        f"@ ${price:,.2f}/oz {metal}",
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
