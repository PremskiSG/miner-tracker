"""Live market data (share price, shares outstanding, market cap in USD) via
yfinance, cached in the market_data table. Companion to fx.py — the financials
give shares outstanding but never a share price, so market cap needs this."""
from __future__ import annotations

import datetime as _dt
import logging

from miner_tracker import db

logger = logging.getLogger("miner_tracker.market")


def _spot_usd(currency: str) -> float:
    """USD per 1 unit of `currency` (multiply local price by this)."""
    if currency.upper() == "USD":
        return 1.0
    import yfinance as yf
    h = yf.Ticker(f"{currency.upper()}USD=X").history(period="5d")
    if h is None or h.empty:
        raise RuntimeError(f"no FX for {currency}USD")
    return float(h["Close"].dropna().iloc[-1])


def fetch(symbol: str) -> dict:
    """Return {as_of, price_local, currency, shares, price_usd, market_cap_usd}."""
    import yfinance as yf
    t = yf.Ticker(symbol)
    fi = t.fast_info
    shares = fi.get("shares")
    currency = fi.get("currency") or "USD"
    hist = t.history(period="5d")
    if hist is None or hist.empty or "Close" not in hist:
        raise RuntimeError(f"no price history for {symbol}")
    close = hist["Close"].dropna()
    price_local = float(close.iloc[-1])
    as_of = close.index[-1].date().isoformat()

    # LSE small-caps quote in pence (GBp); normalise sub-unit quotes to the major unit
    _SUBUNIT = {"GBp": ("GBP", 100), "GBX": ("GBP", 100), "ZAc": ("ZAR", 100),
                "ILA": ("ILS", 100)}
    price = price_local
    if currency in _SUBUNIT:
        currency, div = _SUBUNIT[currency]
        price = price_local / div
    price_usd = price * _spot_usd(currency)
    mcap = price_usd * shares if (price_usd and shares) else None
    return {"symbol": symbol, "as_of": as_of, "price_local": price,  # major-unit
            "currency": currency, "shares": float(shares) if shares else None,
            "price_usd": price_usd, "market_cap_usd": mcap}


def refresh(conn, symbol: str) -> dict:
    d = fetch(symbol)
    db.set_market(conn, symbol, d["as_of"], d["price_local"], d["currency"],
                  d["shares"], d["market_cap_usd"])
    conn.commit()
    logger.info("market: %s $%.3f %s x %s sh -> $%.0fM USD (%s)", symbol,
                d["price_local"], d["currency"], f"{d['shares']:,.0f}" if d["shares"] else "?",
                (d["market_cap_usd"] or 0) / 1e6, d["as_of"])
    return d


def refresh_all(conn) -> None:
    from miner_tracker.config import companies
    for c in companies():
        sym = c.get("market_symbol")
        if not sym:
            continue
        try:
            refresh(conn, sym)
        except Exception as e:  # one bad ticker never sinks the run
            logger.warning("market: %s failed: %s", sym, e)


def latest(conn, market: str, ticker: str) -> dict | None:
    """Cached market row for a company by (market, ticker) — resolves its
    market_symbol from config. None if no symbol or no cached data."""
    from miner_tracker.config import companies
    c = next((c for c in companies()
              if c["market"] == market and c["ticker"] == ticker), None)
    sym = (c or {}).get("market_symbol")
    return db.get_market(conn, sym) if sym else None
