"""Read-side helpers returning pandas DataFrames for the Streamlit UI."""
from __future__ import annotations

import pandas as pd


def companies_frame(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT id, market, ticker, name, reporting_currency, fx_pair, metal "
        "FROM companies", conn)


def metrics_long(conn, company_id: int, quarterly_only: bool = True) -> pd.DataFrame:
    """One row per (period, metric): value, value_usd, currency, confidence..."""
    df = pd.read_sql_query(
        """SELECT period, metric, value, value_usd, currency, unit, confidence,
                  is_derived, needs_review, source_doc_id, source_page
           FROM v_metrics_usd WHERE company_id = ? ORDER BY period""",
        conn, params=(company_id,))
    if quarterly_only:
        df = df[df["period"].str.contains("-Q")]
    return df


def metrics_wide(conn, company_id: int, usd: bool = False) -> pd.DataFrame:
    """Pivot: index=period, columns=metric."""
    long = metrics_long(conn, company_id)
    col = "value_usd" if usd else "value"
    if long.empty:
        return pd.DataFrame()
    return long.pivot_table(index="period", columns="metric", values=col,
                            aggfunc="first").sort_index()


def confidence_wide(conn, company_id: int) -> pd.DataFrame:
    long = metrics_long(conn, company_id)
    if long.empty:
        return pd.DataFrame()
    return long.pivot_table(index="period", columns="metric", values="confidence",
                            aggfunc="first").sort_index()


def reserves_frame(conn, company_id: int) -> pd.DataFrame:
    df = pd.read_sql_query(
        """SELECT statement_date, project, category, metal, tonnage, grade_gpt,
                  confidence
           FROM reserves_statements WHERE company_id = ?
           ORDER BY statement_date, project, category""",
        conn, params=(company_id,))
    if not df.empty:
        df["project"] = df["project"].replace("", "(consolidated)")
    return df


CONSOLIDATED = "(consolidated)"


def reserves_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    """Sum tonnage and tonnage-weight grade across projects, per
    (statement_date, category). Rows with only a grade (no tonnage) fall back to
    a simple mean so a category isn't dropped. Where a report prints BOTH a
    company sub-total (project=(consolidated)) and per-pit rows, the sub-total is
    dropped to avoid double counting."""
    if df.empty:
        return df
    out = []
    for (date, cat), g in df.groupby(["statement_date", "category"]):
        has_pits = (g["project"] != CONSOLIDATED).any()
        if has_pits:
            g = g[g["project"] != CONSOLIDATED]
        tons = g["tonnage"].dropna()
        total_t = tons.sum() if not tons.empty else None
        gw = g.dropna(subset=["tonnage", "grade_gpt"])
        if not gw.empty and gw["tonnage"].sum():
            grade = (gw["tonnage"] * gw["grade_gpt"]).sum() / gw["tonnage"].sum()
        else:
            grade = g["grade_gpt"].dropna().mean() if g["grade_gpt"].notna().any() else None
        out.append({"statement_date": date, "category": cat,
                    "tonnage": total_t, "grade_gpt": grade})
    return pd.DataFrame(out).sort_values(["statement_date", "category"])


OZ_PER_TONNE_GPT = 1_000_000 / 31.1035  # tonnes(Mt-free) * g/t -> troy oz factor
_RESERVE_CATS = ("proved", "probable", "pp")
_RESOURCE_MI = ("measured", "indicated", "measured_indicated")


def _contained_oz(agg: pd.DataFrame, cats) -> float:
    """Contained ounces = sum(tonnage_t * grade_gpt / 31.1035) over categories.
    Uses the split form (proved+probable / measured+indicated) when present,
    else the combined form (pp / measured_indicated) — never both, so a
    company that reports a combined subtotal isn't double-counted."""
    present = set(agg["category"])
    if cats is _RESERVE_CATS:
        use = ["proved", "probable"] if {"proved", "probable"} & present else ["pp"]
    elif cats is _RESOURCE_MI:
        use = (["measured", "indicated"] if {"measured", "indicated"} & present
               else ["measured_indicated"])
    else:
        use = list(cats)
    sub = agg[agg["category"].isin(use)].dropna(subset=["tonnage", "grade_gpt"])
    return float((sub["tonnage"] * sub["grade_gpt"]).sum() / 31.1035)


def mineable_ounces(conn, company_id: int, resource_factor: float = 0.60) -> dict | None:
    """Mineable contained ounces at the latest statement date:
        reserves (P&P, 100%) + resource_factor x M&I resources.
    Inferred is excluded. Returns basis breakdown or None if no reserves data."""
    df = reserves_frame(conn, company_id)
    if df.empty:
        return None
    metal = conn.execute("SELECT metal FROM companies WHERE id=?",
                         (company_id,)).fetchone()["metal"]
    df = df[df["metal"] == metal]
    if df.empty:
        return None
    latest = df["statement_date"].max()
    agg = reserves_aggregate(df[df["statement_date"] == latest])
    reserve_oz = _contained_oz(agg, _RESERVE_CATS)
    resource_oz = _contained_oz(agg, _RESOURCE_MI)
    inferred_oz = _contained_oz(agg, ("inferred",))
    mineable = reserve_oz + resource_factor * resource_oz
    return {
        "mineable_oz": mineable,
        "reserve_oz": reserve_oz,
        "resource_mi_oz": resource_oz,
        "inferred_oz": inferred_oz,
        "resource_factor": resource_factor,
        "statement_date": latest,
        "metal": metal,
        "basis": ("reserves + resource"
                  if reserve_oz and resource_oz else
                  "reserves only" if reserve_oz else "resource only"),
    }


def review_rows(conn, company_id: int) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT m.id, m.period, m.metric, m.value, m.currency, m.unit,
                  m.confidence, m.needs_review, m.source_page, d.path AS source_pdf
           FROM quarterly_metrics m
           LEFT JOIN documents d ON d.id = m.source_doc_id
           WHERE m.company_id = ?
             AND (m.needs_review = 1 OR m.confidence IN ('low', 'medium'))
           ORDER BY m.period DESC, m.metric""",
        conn, params=(company_id,))


def net_debt_usd(conn, company_id: int) -> dict | None:
    """Net debt (total debt - cash) in USD from the most recent quarter that has
    BOTH figures (so they're same-period). Uses the quarterly FX rate; USD
    reporters need none. Returns value + period + FX, or None."""
    crow = conn.execute("SELECT reporting_currency, fx_pair FROM companies WHERE id=?",
                        (company_id,)).fetchone()
    usd_reporter = (crow["reporting_currency"] or "").upper() == "USD"
    row = conn.execute(
        """SELECT d.period,
              (SELECT value FROM quarterly_metrics WHERE company_id=d.company_id
                 AND period=d.period AND metric='debt') AS debt,
              (SELECT value FROM quarterly_metrics WHERE company_id=d.company_id
                 AND period=d.period AND metric='cash') AS cash,
              f.rate AS fx
           FROM quarterly_metrics d
           JOIN companies c ON c.id = d.company_id
           LEFT JOIN fx_rates f ON f.pair = c.fx_pair AND f.period = d.period
           WHERE d.company_id = ? AND d.metric = 'debt' AND d.period LIKE '%-Q%'
             AND (SELECT value FROM quarterly_metrics WHERE company_id=d.company_id
                    AND period=d.period AND metric='cash') IS NOT NULL
           ORDER BY d.period DESC LIMIT 1""", (company_id,)).fetchone()
    if row is None:
        return None
    fx = 1.0 if usd_reporter else row["fx"]
    if fx is None:
        return None
    net_local = (row["debt"] or 0) - (row["cash"] or 0)
    return {"net_debt_usd": net_local * fx, "period": row["period"],
            "debt": row["debt"], "cash": row["cash"], "fx": fx,
            "currency": crow["reporting_currency"]}


def filings_stats(conn, company_id: int, trailing: int = 4) -> dict | None:
    """Forecast anchors computed from extracted filings (metal-aware):
    payability range (reported revenue vs production x realized price, USD),
    trailing production-weighted AISC in USD/oz (reported AISC when the company
    states it, else the (opex + capex)/oz back-calc), trailing annual interest."""
    crow = conn.execute("SELECT metal, reporting_currency FROM companies WHERE id=?",
                        (company_id,)).fetchone()
    metal = crow["metal"]
    usd_reporter = (crow["reporting_currency"] or "").upper() == "USD"
    prod_m, price_m = f"{metal}_production_oz", f"{metal}_price_realized"

    def sub(metric: str) -> str:
        return ("(SELECT value FROM quarterly_metrics WHERE company_id=m.company_id"
                f" AND period=m.period AND metric='{metric}')")

    rows = conn.execute(
        f"""SELECT m.period, m.value AS oz, {sub(f'{metal}_sold_oz')} AS sold_oz,
              {sub('reported_cost')} AS cost, {sub('capex')} AS capex,
              {sub('revenue')} AS revenue, {sub(price_m)} AS price,
              {sub('interest_expense')} AS interest, {sub('depreciation')} AS depreciation,
              {sub('aisc_reported')} AS aisc_rep,
              (SELECT currency FROM quarterly_metrics WHERE company_id=m.company_id
                 AND period=m.period AND metric='aisc_reported') AS aisc_ccy,
              (SELECT currency FROM quarterly_metrics WHERE company_id=m.company_id
                 AND period=m.period AND metric='{price_m}') AS price_ccy,
              f.rate AS fx
           FROM quarterly_metrics m
           JOIN companies c ON c.id = m.company_id
           LEFT JOIN fx_rates f ON f.pair = c.fx_pair AND f.period = m.period
           WHERE m.company_id = ? AND m.metric = '{prod_m}'
             AND m.period LIKE '%-Q%'
           ORDER BY m.period""", (company_id,)).fetchall()
    # USD reporters need no FX (rate 1.0); others require a quarterly rate
    rows = [dict(r) for r in rows]
    for r in rows:
        if r["fx"] is None and usd_reporter:
            r["fx"] = 1.0
    rows = [r for r in rows if r["fx"]]
    if not rows:
        return None

    def usd(value, ccy, fx):
        if value is None:
            return None
        return value if (ccy or "USD") == "USD" else value * fx

    pays = []
    for r in rows:
        price_usd = usd(r["price"], r["price_ccy"], r["fx"])
        oz = r["sold_oz"] or r["oz"]   # revenue corresponds to SOLD ounces
        if r["revenue"] and oz and price_usd:
            pays.append((r["revenue"] * r["fx"]) / (oz * price_usd))

    rep_rows = [r for r in rows if r["aisc_rep"] and r["oz"]][-trailing:]
    if rep_rows:  # company states AISC directly — use it
        aisc = (sum(usd(r["aisc_rep"], r["aisc_ccy"], r["fx"]) * r["oz"]
                    for r in rep_rows) / sum(r["oz"] for r in rep_rows))
        aisc_source = "reported"
    else:
        drv = [r for r in rows if r["cost"] and r["oz"]][-trailing:]
        aisc = (sum((r["cost"] + (r["capex"] or 0)) * r["fx"] for r in drv)
                / sum(r["oz"] for r in drv)) if drv else None
        aisc_source = "derived"
    int_rows = [r for r in rows if r["interest"] is not None][-trailing:]
    interest = sum(r["interest"] * r["fx"] for r in int_rows) if int_rows else None
    dep_rows = [r for r in rows if r["depreciation"] is not None][-trailing:]
    depreciation = (sum(r["depreciation"] * r["fx"] for r in dep_rows)
                    if dep_rows else None)
    return {
        "pay_min": min(pays) if pays else None,
        "pay_avg": sum(pays) / len(pays) if pays else None,
        "pay_max": max(pays) if pays else None,
        "aisc_usd": aisc,
        "aisc_source": aisc_source,
        "interest_usd": interest,
        "depreciation_usd": depreciation,
        "metal": metal,
        "n_quarters": len(rows),
    }


def extraction_runs_frame(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT r.id, d.path, r.model, r.status, r.error, r.input_tokens,
                  r.output_tokens, r.cost_usd, r.finished_at
           FROM extraction_runs r JOIN documents d ON d.id = r.doc_id
           ORDER BY r.id DESC""",
        conn)
