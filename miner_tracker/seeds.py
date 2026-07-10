"""Default NPV scenario seeds. The Sotkamo decks replicate the user's Excel
(soto.xlsx cached values); other companies get a blank template."""
from __future__ import annotations

from miner_tracker import db
from miner_tracker.npv import GlobalInputs, YearInputs, assumptions_from_inputs

_SOSI_PRODUCTION = [1_200_000, 850_000, 1_000_000, 1_100_000, 1_150_000,
                    1_200_000, 1_200_000, 1_200_000] + [1_100_000] * 9
_SOSI_AISC = [35, 50, 47.7, 47.7, 46.64, 46.64, 46.64, 46.64] + [49.82] * 9
_SOSI_INTEREST = [3_780_000, 3_500_000, 2_000_000] + [0] * 14
_SOSI_TAX = [0.2, 0.2, 0.0] + [0.2] * 14

# User's scenario price decks (USD/oz). Gold constants are for phase-2 companies.
SILVER_BEAR, SILVER_BULL = 35.0, 80.0
GOLD_BEAR, GOLD_BULL = 3_000.0, 6_000.0


def _sotkamo_scenario(price: float, payability: float,
                      track_filing_price: bool = False) -> dict:
    # Balance-sheet facts (market_cap, net_debt, shares, depreciation) are NOT
    # hardcoded from the Excel — the NPV page derives them from live market data
    # (yfinance) + filings. Only the forward operating deck is seeded here.
    g = GlobalInputs(payability=payability, mining_tax=0.025, discount_rate=0.10)
    years = [YearInputs(year=2024 + i, production_oz=_SOSI_PRODUCTION[i],
                        aisc=_SOSI_AISC[i], price=price,
                        interest=_SOSI_INTEREST[i], tax_rate=_SOSI_TAX[i])
             for i in range(17)]
    out = assumptions_from_inputs(g, years)
    out["ui"] = {"track_filing_price": track_filing_price}
    return out


def blank_scenario(start_year: int = 2026, n_years: int = 15,
                   price: float = 0.0, payability: float = 1.0,
                   track_filing_price: bool = False) -> dict:
    g = GlobalInputs(payability=payability, mining_tax=0.0, discount_rate=0.10)
    years = [YearInputs(year=start_year + i, price=price) for i in range(n_years)]
    out = assumptions_from_inputs(g, years)
    out["ui"] = {"track_filing_price": track_filing_price}
    return out


def seed_company_scenarios(conn, company_id: int, ticker: str,
                           metal: str = "silver") -> None:
    """Create default scenarios if the company has none."""
    if db.load_scenarios(conn, company_id):
        return
    if ticker == "SOSI1":
        # payabilities per user: spot = filings average, bear = bottom of the
        # filings range, bull = top (refine via the page's filings caption)
        db.save_scenario(conn, company_id, "spot", _sotkamo_scenario(58.0, 1.20))
        db.save_scenario(conn, company_id, "bear", _sotkamo_scenario(SILVER_BEAR, 1.05))
        db.save_scenario(conn, company_id, "bull", _sotkamo_scenario(SILVER_BULL, 1.38))
    elif metal == "gold":
        # user's gold decks: bear $3,000 / bull $6,000; spot tracks the filings
        db.save_scenario(conn, company_id, "spot",
                         blank_scenario(track_filing_price=True))
        db.save_scenario(conn, company_id, "bear", blank_scenario(price=GOLD_BEAR))
        db.save_scenario(conn, company_id, "bull", blank_scenario(price=GOLD_BULL))
    else:
        db.save_scenario(conn, company_id, "base", blank_scenario())
    conn.commit()
