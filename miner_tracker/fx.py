"""Quarterly-average FX rates via yfinance, cached in the fx_rates table.

Pair convention: 'SEKUSD' = USD per 1 SEK (so reported values MULTIPLY by the
rate to convert to USD, matching the v_metrics_usd view).
"""
from __future__ import annotations

import logging

from miner_tracker import db

logger = logging.getLogger("miner_tracker.fx")


def quarterly_rates(pair: str, start_year: int) -> dict[str, float]:
    import yfinance as yf

    data = yf.download(f"{pair}=X", start=f"{start_year}-01-01",
                       progress=False, auto_adjust=True)
    if data is None or data.empty:
        raise RuntimeError(f"no FX data for {pair}=X")
    close = data["Close"]
    if hasattr(close, "columns"):  # yfinance may return a 1-col DataFrame
        close = close.iloc[:, 0]
    grouped = close.groupby(close.index.to_period("Q")).mean()
    return {f"{p.year}-Q{p.quarter}": float(v) for p, v in grouped.items()}


def refresh(conn, pair: str, start_year: int = 2021) -> int:
    rates = quarterly_rates(pair, start_year)
    for period, rate in rates.items():
        db.set_fx(conn, pair, period, rate)
    conn.commit()
    logger.info("fx: stored %d quarterly rates for %s", len(rates), pair)
    return len(rates)


def refresh_all(conn, start_year: int = 2021) -> None:
    from miner_tracker.config import companies

    pairs = {c["fx_pair"] for c in companies() if c.get("fx_pair")}
    for pair in sorted(pairs):
        refresh(conn, pair, start_year)
