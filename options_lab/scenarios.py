"""Deterministic stress assumptions, not estimated historical return distributions."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
import numpy as np

from .models import MarketSnapshot, Settings, utc


@dataclass(frozen=True)
class Scenario:
    name: str
    category: str
    parallel_dollars: float = 0.0
    twist_dollars: float = 0.0
    normal_vol_factor: float = 1.0
    lognormal_vol_factor: float = 1.0
    elapsed_days: float = 0.0
    normal_vol_additive: float = 0.0


ASSUMPTIONS = [
    "Deterministic synthetic stress grid anchored to observed prices; no scenario probabilities or VaR claim.",
    "Each future receives the same parallel USD/bbl shock plus a linear expiry-rank twist.",
    "Common-vol scenarios multiply normal and lognormal vols together; CSO-only basis shocks are explicit exceptions.",
    "The absolute CSO basis scenario adds 1 USD/bbl/sqrt(year) of normal volatility, including when initial normal vol is zero; vanilla vol is unchanged. This magnitude is an explicit stress assumption, not a forecast.",
    "Volatility mappings are editable-model assumptions, not fitted correlations or historical hedge betas.",
    "Options are fully repriced using fixed-strike vol slices and ACT/365 time; no linear-Greek P&L approximation.",
    "Stress P&L is the change from active model mid; observed BBO half-spread (or a stated tick assumption) plus fees is deducted. Model-versus-market entry basis is not credited; this is not realized execution P&L.",
]


def build_scenarios(settings: Settings) -> tuple[Scenario, ...]:
    horizon = settings.horizon_days
    rows = [
        Scenario("time_only", "carry", elapsed_days=horizon),
        Scenario("oil_up_5", "parallel", 5.0, elapsed_days=horizon),
        Scenario("oil_down_5", "parallel", -5.0, elapsed_days=horizon),
        Scenario("curve_steepens", "curve", twist_dollars=2.0, elapsed_days=horizon),
        Scenario("common_vol_up_25pct", "common_vol", normal_vol_factor=1.25, lognormal_vol_factor=1.25, elapsed_days=horizon),
        Scenario("cso_basis_vol_up_50pct", "basis", normal_vol_factor=1.5, elapsed_days=horizon),
        Scenario("cso_basis_vol_up_1_normal", "basis", normal_vol_additive=1.0, elapsed_days=horizon),
    ]
    # Keep both relative and absolute basis stresses even for the minimum
    # seven-case configuration; the explicit reverse twist follows at eight.
    if settings.scenario_count >= 8:
        rows.append(Scenario("curve_flattens", "curve", twist_dollars=-2.0, elapsed_days=horizon))
    # A deterministic bounded lattice, with enough points for the requested
    # count. No random draw or fitted distribution is implied by these points.
    grid = []
    needed = settings.scenario_count - len(rows)
    levels = max(3, int(np.ceil(max(1, needed) ** (1 / 3))))
    while levels ** 3 < needed:
        levels += 1
    for shift in np.linspace(-8., 8., levels):
        for twist in np.linspace(-3., 3., levels):
            for factor in np.linspace(.75, 1.35, levels):
                grid.append(Scenario(f"grid_{shift:g}_{twist:g}_{factor:g}", "joint",
                                     shift, twist, factor, factor, horizon))
    remaining = needed
    # Spread any requested subset across the entire grid, rather than selecting
    # the negative-price-shock prefix. These are still unweighted stress cases.
    if remaining:
        rows.extend(grid[i] for i in np.linspace(0, len(grid) - 1, remaining, dtype=int))
    return tuple(rows)


def shocked_snapshot(snapshot: MarketSnapshot, scenario: Scenario) -> MarketSnapshot:
    futures = sorted((c for c in snapshot.contracts if c.kind == "future"), key=lambda c: (c.expiry, c.contract_id))
    coordinates = dict(zip((c.contract_id for c in futures), np.linspace(0.5, -0.5, len(futures))))
    at = (utc(snapshot.as_of) + timedelta(days=scenario.elapsed_days)).isoformat()
    quotes = []
    for q in snapshot.quotes:
        shift = scenario.parallel_dollars + scenario.twist_dollars * coordinates[q.contract_id] if q.contract_id in coordinates else 0.0
        quotes.append(replace(q, as_of=at,
                              bid=None if q.bid is None else q.bid + shift,
                              ask=None if q.ask is None else q.ask + shift,
                              mark=None if q.mark is None else q.mark + shift))
    return replace(snapshot, quotes=tuple(quotes), as_of=at, received_at=None, mode="synthetic_stress")


def scenario_vol(kind: str, vol: float, scenario: Scenario) -> float:
    return (vol * scenario.normal_vol_factor + scenario.normal_vol_additive
            if kind == "cso" else vol * scenario.lognormal_vol_factor)


def risk_summary(pnl) -> dict:
    values = np.asarray(pnl, dtype=float)
    if values.size == 0:
        return dict(worst_abs_pnl=None, worst_loss=None, scenario_rms=None, scenario_mean=None, scenario_count=0)
    if not np.isfinite(values).all():
        raise ValueError("Non-finite scenario P&L")
    return dict(worst_abs_pnl=float(np.max(np.abs(values))), worst_loss=float(max(0.0, -np.min(values))),
                scenario_rms=float(np.sqrt(np.mean(values ** 2))), scenario_mean=float(np.mean(values)),
                scenario_count=int(len(values)))
