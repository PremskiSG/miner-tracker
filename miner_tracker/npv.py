"""Pure-python NPV engine mirroring the user's Excel model (soto.xlsx, verified
against cached values):

    revenue_y = production_oz * price * payability * fx
    cost_y    = production_oz * aisc * fx * (1 + mining_tax)
    ni_y      = (revenue - cost - depreciation) * (1 - tax_rate) - interest - capex + depreciation

Discounting starts at year index `discount_start_index` (Excel: first two model
years contribute nothing) with exponent (i - 1):

    accumulated_dcf_i = sum over j in [start..i] of ni_j / (1 + r)^(j - 1)
    npv               = accumulated_dcf at the final year
    upside            = (npv - ev) / market_cap
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class GlobalInputs:
    payability: float = 1.0        # byproduct payability multiplier on revenue
    mining_tax: float = 0.0        # e.g. 0.025 Finnish mining tax, applied on cost
    discount_rate: float = 0.10
    market_cap: float = 0.0        # absolute USD
    net_debt: float = 0.0          # absolute USD; EV = market_cap + net_debt
    shares_outstanding: float | None = None  # millions, informational
    discount_start_index: int = 2  # first year index that enters the NPV sum


@dataclass
class YearInputs:
    year: int
    production_oz: float = 0.0
    aisc: float = 0.0              # per ounce, silver-only without byproduct
    price: float = 0.0             # per ounce
    fx: float = 1.0
    capex: float = 0.0
    depreciation: float = 0.0
    interest: float = 0.0
    tax_rate: float = 0.2
    ev: float | None = None        # per-year EV override (Excel has one)


@dataclass
class YearResult:
    year: int
    revenue: float
    cost: float
    net_income: float
    discounted_cf: float           # this year's contribution to the NPV sum (0 before start)
    accumulated_dcf: float
    ev_fcf: float
    pe: float


@dataclass
class ModelResult:
    years: list[YearResult] = field(default_factory=list)
    npv: float = 0.0
    ev: float = 0.0
    ev_npv: float = math.nan
    upside: float = math.nan


def compute(g: GlobalInputs, years: list[YearInputs]) -> ModelResult:
    ev_default = g.market_cap + g.net_debt
    acc = 0.0
    out = ModelResult(ev=ev_default)
    for i, y in enumerate(years):
        revenue = y.production_oz * y.price * g.payability * y.fx
        cost = y.production_oz * y.aisc * y.fx * (1 + g.mining_tax)
        ni = (revenue - cost - y.depreciation) * (1 - y.tax_rate) - y.interest - y.capex + y.depreciation
        if i >= g.discount_start_index:
            # first counted year is discounted by 1 period; the exponent is
            # relative to discount_start_index so it works whether the deck
            # starts "now" (index 0) or skips leading historical years.
            # (i - discount_start_index + 1) == (i - 1) when start index is 2,
            # so the Sotkamo/Excel-validated NPV is unchanged.
            dcf = ni / (1 + g.discount_rate) ** (i - g.discount_start_index + 1)
            acc += dcf
        else:
            dcf = 0.0
        ev_y = y.ev if y.ev is not None else ev_default
        out.years.append(YearResult(
            year=y.year, revenue=revenue, cost=cost, net_income=ni,
            discounted_cf=dcf, accumulated_dcf=acc,
            ev_fcf=ev_y / ni if ni else math.nan,
            pe=g.market_cap / ni if ni else math.nan,
        ))
    out.npv = acc
    out.ev_npv = ev_default / out.npv if out.npv else math.nan
    out.upside = (out.npv - ev_default) / g.market_cap if g.market_cap else math.nan
    return out


@dataclass
class MineLife:
    years: float                    # total years of production the reserves support
    depletion_year: int | None      # calendar year reserves run out (None = not within horizon)
    remaining_oz: float             # ounces still in the ground after the forecast horizon
    consumed_oz: float              # ounces the forecast produces (capped at mineable)


def carry_forward_aisc(years: list[YearInputs]) -> None:
    """In place: a year with no AISC (None/0) inherits the previous year's."""
    last = 0.0
    for y in years:
        if y.aisc:
            last = y.aisc
        elif last:
            y.aisc = last


@dataclass
class PlanLife:
    years: float              # duration of the production plan (last-first+1)
    start_year: int | None
    end_year: int | None      # last year the plan produces
    planned_oz: float         # total ounces the plan produces


def plan_mine_life(years: list[YearInputs], from_year: int | None = None) -> PlanLife:
    """Mine life implied by the production PLAN itself — the span of years the
    forecast actually produces, and the total ounces it plans to mine. This is
    the company's stated plan, independent of classified reserves."""
    prod = [(y.year, y.production_oz or 0.0)
            for y in sorted(years, key=lambda y: y.year)
            if (from_year is None or y.year >= from_year) and (y.production_oz or 0) > 0]
    if not prod:
        return PlanLife(0.0, None, None, 0.0)
    start, end = prod[0][0], prod[-1][0]
    return PlanLife(float(end - start + 1), start, end, sum(p for _, p in prod))


def mine_life(mineable_oz: float, years: list[YearInputs],
              from_year: int | None = None) -> MineLife:
    """Walk the production forecast, depleting `mineable_oz` year by year.
    Returns full years supported (fractional in the final year) and the
    calendar year of depletion. Payability/recovery are ignored — mine life is
    contained ounces vs planned production."""
    remaining = mineable_oz
    consumed = 0.0
    life = 0.0
    for y in sorted(years, key=lambda y: y.year):
        if from_year is not None and y.year < from_year:
            continue
        prod = y.production_oz or 0.0
        if prod <= 0:
            continue
        if remaining <= prod:
            life += remaining / prod
            consumed += remaining
            return MineLife(round(life, 2), y.year, 0.0, consumed)
        remaining -= prod
        consumed += prod
        life += 1
    return MineLife(round(life, 2), None, remaining, consumed)


def years_from_assumptions(assumptions: dict) -> tuple[GlobalInputs, list[YearInputs]]:
    """Rebuild engine inputs from a scenario's persisted JSON:
    {"globals": {...}, "years": [{...}, ...]}"""
    g = GlobalInputs(**assumptions.get("globals", {}))
    years = [YearInputs(**y) for y in assumptions.get("years", [])]
    return g, years


def assumptions_from_inputs(g: GlobalInputs, years: list[YearInputs]) -> dict:
    return {
        "globals": {k: v for k, v in g.__dict__.items()},
        "years": [{k: v for k, v in y.__dict__.items()} for y in years],
    }
