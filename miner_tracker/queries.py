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


def filings_stats(conn, company_id: int, trailing: int = 4) -> dict | None:
    """Forecast anchors computed from extracted filings (metal-aware):
    payability range (reported revenue vs production x realized price, USD),
    trailing production-weighted AISC in USD/oz (reported AISC when the company
    states it, else the (opex + capex)/oz back-calc), trailing annual interest."""
    metal = conn.execute("SELECT metal FROM companies WHERE id=?",
                         (company_id,)).fetchone()["metal"]
    prod_m, price_m = f"{metal}_production_oz", f"{metal}_price_realized"

    def sub(metric: str) -> str:
        return ("(SELECT value FROM quarterly_metrics WHERE company_id=m.company_id"
                f" AND period=m.period AND metric='{metric}')")

    rows = conn.execute(
        f"""SELECT m.period, m.value AS oz, {sub(f'{metal}_sold_oz')} AS sold_oz,
              {sub('reported_cost')} AS cost, {sub('capex')} AS capex,
              {sub('revenue')} AS revenue, {sub(price_m)} AS price,
              {sub('interest_expense')} AS interest,
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
    return {
        "pay_min": min(pays) if pays else None,
        "pay_avg": sum(pays) / len(pays) if pays else None,
        "pay_max": max(pays) if pays else None,
        "aisc_usd": aisc,
        "aisc_source": aisc_source,
        "interest_usd": interest,
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
