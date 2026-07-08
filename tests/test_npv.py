"""Fixtures are cached values read from the user's soto.xlsx (Sotkamo sheet,
NPV block rows 112-137) — the engine must reproduce the Excel exactly."""
import pytest

from miner_tracker.npv import GlobalInputs, YearInputs, compute

PRODUCTION = [1_200_000, 850_000, 1_000_000, 1_100_000, 1_150_000,
              1_200_000, 1_200_000, 1_200_000] + [1_100_000] * 9
AISC = [35, 50, 47.7, 47.7, 46.64, 46.64, 46.64, 46.64] + [49.82] * 9
INTEREST = [3_780_000, 3_500_000, 2_000_000] + [0] * 14
TAX = [0.2, 0.2, 0.0] + [0.2] * 14
DEPRECIATION = 7_461_449.179240591
EV_2024_25 = 61_375_432.664694116
EV_LATER = 170_884_629.41469073
MARKET_CAP = 154_200_000.0
NET_DEBT = EV_LATER - MARKET_CAP


def spot_inputs(price: float = 60.79):
    g = GlobalInputs(payability=1.18, mining_tax=0.025, discount_rate=0.1,
                     market_cap=MARKET_CAP, net_debt=NET_DEBT)
    years = [
        YearInputs(year=2024 + i, production_oz=PRODUCTION[i], aisc=AISC[i],
                   price=price, depreciation=DEPRECIATION, interest=INTEREST[i],
                   tax_rate=TAX[i], ev=EV_2024_25 if i < 2 else EV_LATER)
        for i in range(17)
    ]
    return g, years


def test_spot_scenario_matches_excel():
    g, years = spot_inputs()
    res = compute(g, years)

    assert res.years[0].revenue == pytest.approx(86_078_640, rel=1e-9)
    assert res.years[0].cost == pytest.approx(43_050_000, rel=1e-9)
    assert res.years[0].net_income == pytest.approx(32_135_201.835848127, rel=1e-9)
    # 2026: tax pool year (rate 0)
    assert res.years[2].net_income == pytest.approx(20_839_700.0, rel=1e-9)
    assert res.years[2].accumulated_dcf == pytest.approx(18_945_181.818181824, rel=1e-9)
    assert res.npv == pytest.approx(164_125_157.29022422, rel=1e-9)
    assert res.ev_npv == pytest.approx(1.0411848630403056, rel=1e-9)
    assert res.upside == pytest.approx(-0.043835746591871005, rel=1e-9)
    assert res.years[0].ev_fcf == pytest.approx(1.9099127797052553, rel=1e-9)
    assert res.years[0].pe == pytest.approx(4.798476162921859, rel=1e-9)


def test_first_two_years_not_discounted():
    g, years = spot_inputs()
    res = compute(g, years)
    assert res.years[0].accumulated_dcf == 0.0
    assert res.years[1].accumulated_dcf == 0.0
    assert res.years[0].discounted_cf == 0.0


def test_bear_scenario_matches_excel():
    # Excel quirk: the bear block displays payability 0.85 but its formulas
    # reference the spot 1.18 — fixture replicates the cached number.
    g, years = spot_inputs(price=20.0)
    res = compute(g, years)
    assert res.npv == pytest.approx(-171_932_234.4776868, rel=1e-9)


def test_assumptions_roundtrip():
    from miner_tracker.npv import assumptions_from_inputs, years_from_assumptions
    g, years = spot_inputs()
    g2, years2 = years_from_assumptions(assumptions_from_inputs(g, years))
    assert compute(g2, years2).npv == pytest.approx(compute(g, years).npv, rel=1e-12)
