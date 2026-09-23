"""European research pricers. Prices are USD/bbl, never contract dollars.

Normal vol is USD/bbl/sqrt(year); Black vol is a decimal annual volatility.
The IV inversion round trip is a numerical check, not market validation.
"""
from __future__ import annotations

import math
from scipy.optimize import brentq
from scipy.special import ndtr

from .models import Contract, MarketSnapshot, utc

YEAR_SECONDS = 365.0 * 24 * 60 * 60


def _inputs(kind, forward, strike, tau, vol, right, rate):
    if kind not in ("future", "cso", "vanilla"):
        raise ValueError("Unknown pricing kind")
    values = (forward, tau, vol, rate) if kind == "future" else (forward, strike, tau, vol, rate)
    if any(v is None or not math.isfinite(v) for v in values):
        raise ValueError("Pricing inputs must be finite")
    if tau < 0 or vol < 0:
        raise ValueError("Negative maturity or volatility")
    if kind != "future" and right not in ("call", "put"):
        raise ValueError("Option right must be call or put")
    if kind == "vanilla" and (forward <= 0 or strike <= 0):
        raise ValueError("Black76 requires strictly positive forward and strike")
    if abs(rate * tau) > 700:
        raise ValueError("Discount factor outside numerical range")


def price(kind, forward, strike, tau, vol, right, rate=0.0) -> float:
    """Discounted Bachelier / Black76 option value, or undiscounted future mark."""
    _inputs(kind, forward, strike, tau, vol, right, rate)
    if kind == "future":
        return float(forward)
    sign = 1.0 if right == "call" else -1.0
    discount = math.exp(-rate * tau)
    if tau == 0 or vol == 0:
        return discount * max(sign * (forward - strike), 0.0)
    width = vol * math.sqrt(tau)
    if kind == "cso":
        d = sign * (forward - strike) / width
        value = sign * (forward - strike) * ndtr(d) + width * math.exp(-0.5 * d * d) / math.sqrt(2 * math.pi)
    else:
        d1 = math.log(forward / strike) / width + width / 2
        d2 = d1 - width
        value = sign * (forward * ndtr(sign * d1) - strike * ndtr(sign * d2))
    return float(discount * max(value, 0.0))


def implied_vol(kind, forward, strike, tau, observed_price, right, rate=0.0) -> float:
    """Invert the declared model. Invalid / expiry / infinite-IV observations fail."""
    _inputs(kind, forward, strike, tau, 0.0, right, rate)
    if kind == "future" or tau == 0:
        raise ValueError("Implied volatility is not identifiable for a future or at expiry")
    if not math.isfinite(observed_price) or observed_price < 0:
        raise ValueError("Invalid observed option price")
    intrinsic = price(kind, forward, strike, tau, 0.0, right, rate)
    tolerance = 1e-11 * max(1.0, abs(forward), abs(strike), observed_price)
    if observed_price < intrinsic - tolerance:
        raise ValueError("Observed option price below discounted intrinsic")
    if abs(observed_price - intrinsic) <= tolerance:
        return 0.0
    if kind == "vanilla":
        upper = math.exp(-rate * tau) * (forward if right == "call" else strike)
        if observed_price >= upper:
            raise ValueError("Observed price reaches Black76 infinite-volatility upper bound")
    high = 1.0
    for _ in range(40):
        if price(kind, forward, strike, tau, high, right, rate) >= observed_price:
            break
        high *= 2.0
    else:
        raise ValueError("Unable to bracket implied volatility")
    return float(brentq(lambda v: price(kind, forward, strike, tau, v, right, rate) - observed_price,
                        0.0, high, xtol=1e-12, rtol=1e-12))


def greeks(kind, forward, strike, tau, vol, right, rate=0.0) -> dict:
    """Model-native derivatives plus an explicit economic volatility bump.

Raw normal and lognormal vega must not be aggregated. Theta is the value
change per calendar year passing with the futures mark held constant.
"""
    value = price(kind, forward, strike, tau, vol, right, rate)
    bump = 0.1 if kind == "cso" else 0.01
    unit = "usd_per_bbl_sqrt_year" if kind == "cso" else "decimal_annual"
    if kind == "future":
        return dict(delta=1.0, gamma=0.0, vega=0.0, theta=0.0, vega_unit="not_applicable",
                    vega_bump=0.0, vega_bump_price_change=0.0, boundary=False)
    discount = math.exp(-rate * tau)
    sign = 1 if right == "call" else -1
    boundary = tau == 0 or vol == 0
    boundary_note = ""
    if boundary:
        delta = discount * (sign if sign * (forward - strike) > 0 else (sign * 0.5 if forward == strike else 0.0))
        gamma = None if forward == strike else 0.0
        vega = (discount * math.sqrt(tau) * (forward if kind == "vanilla" else 1.0) / math.sqrt(2 * math.pi)
                if tau > 0 and forward == strike else 0.0)
        theta = None if tau == 0 else rate * value
        boundary_note = ("Zero-width boundary: ATM delta uses the symmetric limit; ATM gamma is undefined. "
                         "At zero vol and positive maturity, vega is the one-sided derivative; expiry theta is undefined.")
    else:
        width = vol * math.sqrt(tau)
        d = (forward - strike) / width if kind == "cso" else math.log(forward / strike) / width + width / 2
        density = math.exp(-0.5 * d * d) / math.sqrt(2 * math.pi)
        delta = discount * (ndtr(d) - (1.0 if right == "put" else 0.0))
        gamma = discount * density / (width * (forward if kind == "vanilla" else 1.0))
        vega = discount * density * math.sqrt(tau) * (forward if kind == "vanilla" else 1.0)
        theta = rate * value - vega * vol / (2 * tau)
    return dict(delta=float(delta), gamma=None if gamma is None else float(gamma), vega=float(vega),
                theta=None if theta is None else float(theta), boundary_note=boundary_note,
                vega_unit=unit, vega_bump=bump,
                vega_bump_price_change=price(kind, forward, strike, tau, vol + bump, right, rate) - value,
                boundary=boundary)


def time_to_expiry(contract: Contract, snapshot: MarketSnapshot) -> float:
    return max(0.0, (utc(contract.expiry) - utc(snapshot.as_of)).total_seconds() / YEAR_SECONDS)


def forward_for_contract(contract: Contract, snapshot: MarketSnapshot) -> float:
    ids = (contract.contract_id,) if contract.kind == "future" else contract.underlyings
    marks = []
    for identifier in ids:
        underlying = snapshot.contract_map.get(identifier)
        q = snapshot.quote_map.get(identifier)
        if underlying is None or underlying.kind != "future" or q is None or q.mid is None:
            raise ValueError(f"Missing exact underlying future mark: {identifier}")
        marks.append(q.mid)
    return float(marks[0] - marks[1] if contract.kind == "cso" else marks[0])


def instrument_value(contract: Contract, snapshot: MarketSnapshot, vol: float) -> float:
    return price(contract.kind, forward_for_contract(contract, snapshot), contract.strike,
                 time_to_expiry(contract, snapshot), vol, contract.right, snapshot.rate)
