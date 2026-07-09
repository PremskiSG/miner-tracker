"""Read-side helpers returning pandas DataFrames for the Streamlit UI."""
from __future__ import annotations

import pandas as pd


def companies_frame(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT id, market, ticker, name, reporting_currency, fx_pair FROM companies",
        conn)


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
    return pd.read_sql_query(
        """SELECT statement_date, category, metal, tonnage, grade_gpt, confidence
           FROM reserves_statements WHERE company_id = ?
           ORDER BY statement_date, category""",
        conn, params=(company_id,))


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
    """Forecast anchors computed from extracted filings:
    payability range (reported revenue vs production x realized price, USD),
    trailing production-weighted AISC (USD/oz), trailing annual interest (USD)."""
    rows = conn.execute(
        """SELECT m.period,
             (SELECT value FROM quarterly_metrics WHERE company_id=m.company_id
                AND period=m.period AND metric='reported_cost')          AS cost,
             (SELECT value FROM quarterly_metrics WHERE company_id=m.company_id
                AND period=m.period AND metric='capex')                  AS capex,
             (SELECT value FROM quarterly_metrics WHERE company_id=m.company_id
                AND period=m.period AND metric='silver_production_oz')   AS oz,
             (SELECT value FROM quarterly_metrics WHERE company_id=m.company_id
                AND period=m.period AND metric='silver_price_realized')  AS price,
             (SELECT value FROM quarterly_metrics WHERE company_id=m.company_id
                AND period=m.period AND metric='interest_expense')       AS interest,
             m.value AS revenue, f.rate AS fx
           FROM quarterly_metrics m
           JOIN companies c ON c.id = m.company_id
           LEFT JOIN fx_rates f ON f.pair = c.fx_pair AND f.period = m.period
           WHERE m.company_id = ? AND m.metric = 'revenue' AND m.period LIKE '%-Q%'
           ORDER BY m.period""", (company_id,)).fetchall()
    rows = [r for r in rows if r["fx"]]
    if not rows:
        return None
    pays = [(r["revenue"] * r["fx"]) / (r["oz"] * r["price"])
            for r in rows if r["oz"] and r["price"]]
    aisc_rows = [r for r in rows if r["cost"] and r["oz"]][-trailing:]
    aisc = (sum((r["cost"] + (r["capex"] or 0)) * r["fx"] for r in aisc_rows)
            / sum(r["oz"] for r in aisc_rows)) if aisc_rows else None
    int_rows = [r for r in rows if r["interest"] is not None][-trailing:]
    interest = sum(r["interest"] * r["fx"] for r in int_rows) if int_rows else None
    return {
        "pay_min": min(pays) if pays else None,
        "pay_avg": sum(pays) / len(pays) if pays else None,
        "pay_max": max(pays) if pays else None,
        "aisc_usd": aisc,
        "interest_usd": interest,
        "n_quarters": len(rows),
    }


def extraction_runs_frame(conn) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT r.id, d.path, r.model, r.status, r.error, r.input_tokens,
                  r.output_tokens, r.cost_usd, r.finished_at
           FROM extraction_runs r JOIN documents d ON d.id = r.doc_id
           ORDER BY r.id DESC""",
        conn)
