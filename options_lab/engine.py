"""Atomic pure evaluation of pricing, indicative quotes, stress risk and hedges."""
from __future__ import annotations

import math
from collections import defaultdict
import numpy as np

from .hedging import optimize_hedge
from .models import ENGINE_VERSION, Contract, MarketSnapshot, Position, Settings, VolVersion, portfolio_id, stable_id, utc
from .pricing import forward_for_contract, greeks, instrument_value, time_to_expiry
from .scenarios import ASSUMPTIONS, build_scenarios, risk_summary, scenario_vol, shocked_snapshot


def _freshness(contract: Contract, snapshot: MarketSnapshot, settings: Settings) -> list[str]:
    reasons = []
    if not snapshot.feed_alive:
        reasons.append("Source is disconnected")
    identifiers = set(contract.underlyings if contract.kind != "future" else (contract.contract_id,))
    identifiers.add(contract.contract_id)
    for identifier in identifiers:
        if utc(snapshot.contract_map[identifier].expiry) <= utc(snapshot.as_of):
            reasons.append(f"Expired required contract: {identifier}")
        quote = snapshot.quote_map.get(identifier)
        if quote is None or quote.mid is None:
            reasons.append(f"Missing observed mark: {identifier}")
            continue
        if quote.flags:
            reasons.append(f"Flagged source quote: {identifier} ({', '.join(quote.flags)})")
        if snapshot.contract_map[identifier].kind != "future" and any(v is not None and v < 0 for v in (quote.bid, quote.ask, quote.mark)):
            reasons.append(f"Negative observed option price: {identifier}")
        age = (utc(snapshot.as_of) - utc(quote.as_of)).total_seconds()
        limit = settings.max_underlying_age_seconds if snapshot.contract_map[identifier].kind == "future" else settings.max_quote_age_seconds
        if age > limit:
            reasons.append(f"Stale observed mark: {identifier} ({age:g}s)")
    if snapshot.mode.startswith("live"):
        if not snapshot.received_at:
            reasons.append("Missing live transport timestamp")
        elif (utc(snapshot.received_at) - utc(snapshot.as_of)).total_seconds() > settings.max_transport_delay_seconds:
            reasons.append("Live transport delay exceeded")
    return reasons


def _vol_freshness(contract, version, snapshot, settings):
    """Follow-market vols inherit the age of the actual bracketing observations.

An explicitly pinned manual version intentionally persists through market
refresh; its age is visible in the editor instead of silently replacing it.
"""
    if contract.kind == "future" or version.origin == "manual":
        return []
    nodes = sorted((n for n in version.nodes if n.slice_key == contract.slice_key), key=lambda n: n.strike)
    lower = [n for n in nodes if n.strike <= contract.strike]
    upper = [n for n in nodes if n.strike >= contract.strike]
    if not lower or not upper:
        return [f"Missing volatility interpolation coverage: {contract.contract_id}"]
    used = {n.node_id: n for n in (lower[-1], upper[0])}
    return [f"Stale calibrated volatility node: {n.contract_id or n.node_id} ({(utc(snapshot.as_of) - utc(n.as_of)).total_seconds():g}s)"
            for n in used.values() if (utc(snapshot.as_of) - utc(n.as_of)).total_seconds() > settings.max_quote_age_seconds]


def _cost(contract, snapshot, settings):
    quote = snapshot.quote_map.get(contract.contract_id)
    if quote and quote.bid is not None and quote.ask is not None and quote.kind == "bbo":
        half_spread = (quote.ask - quote.bid) / 2
        source = "observed BBO half-spread + configured fee"
        bid_size, ask_size = quote.bid_size, quote.ask_size
    else:
        half_spread = contract.tick_size
        source = "assumed one tick crossing cost per side + configured fee; no observed BBO"
        bid_size = ask_size = None
    return half_spread * contract.multiplier + settings.fee_per_contract, source, bid_size, ask_size


def _indicative_quote(contract, value, unit_pnl, base_pnl, position, snapshot, settings, reasons):
    result = dict(contract_id=contract.contract_id, model_price=value, bid=None, ask=None,
                  bid_size=0, ask_size=0, bid_marginal_risk=None, ask_marginal_risk=None,
                  status="suppressed", reasons=list(reasons), label="Indicative model quote; no orders or fill prediction")
    if reasons:
        return result
    risk = float(np.max(np.abs(base_pnl)))
    buy_risk = float(np.max(np.abs(base_pnl + unit_pnl)))
    sell_risk = float(np.max(np.abs(base_pnl - unit_pnl)))
    marginal_buy, marginal_sell = buy_risk - risk, sell_risk - risk
    penalty_buy = (buy_risk ** 2 - risk ** 2) / (2 * settings.risk_scale_dollars)
    penalty_sell = (sell_risk ** 2 - risk ** 2) / (2 * settings.risk_scale_dollars)
    cost, source, _, _ = _cost(contract, snapshot, settings)
    half_width = max(contract.tick_size, cost / contract.multiplier)
    raw_bid = value - half_width - settings.risk_aversion * penalty_buy / contract.multiplier
    raw_ask = value + half_width + settings.risk_aversion * penalty_sell / contract.multiplier
    # Convex max-absolute stress risk implies raw_bid <= raw_ask. Clip and round outward.
    ask = max(contract.tick_size, math.ceil((raw_ask - 1e-12) / contract.tick_size) * contract.tick_size)
    bid = max(0.0, min(ask - contract.tick_size, math.floor((raw_bid + 1e-12) / contract.tick_size) * contract.tick_size))
    sizes, minimum_clips = [], []
    for sign in (1, -1):
        allowed = 0
        minimum_compliant = 0
        for k in range(1, settings.clip_size + 1):
            next_risk = float(np.max(np.abs(base_pnl + sign * k * unit_pnl)))
            within_risk = next_risk <= settings.risk_limit_dollars or (risk > settings.risk_limit_dollars and next_risk < risk - 1e-9)
            if abs(position + sign * k) <= settings.position_limit and within_risk:
                allowed = k
                minimum_compliant = minimum_compliant or k
        sizes.append(allowed)
        minimum_clips.append(minimum_compliant)
    if raw_bid < 0:
        sizes[0] = 0
        minimum_clips[0] = 0
    if raw_ask < 0:
        sizes[1] = 0
        minimum_clips[1] = 0
    result.update(bid=float(round(bid, 12)), ask=float(round(ask, 12)), bid_size=sizes[0], ask_size=sizes[1],
                  bid_marginal_risk=marginal_buy, ask_marginal_risk=marginal_sell, cost_source=source,
                  bid_marginal_penalty=penalty_buy, ask_marginal_penalty=penalty_sell,
                  initial_position_breach=abs(position) > settings.position_limit,
                  minimum_compliant_bid_clip=minimum_clips[0], minimum_compliant_ask_clip=minimum_clips[1],
                  position_policy="A complete indicated clip must leave the final position within its limit; partial repair is not labelled compliant. Existing stress-risk excess permits only stress-reducing clips, still subject to final position compliance. No partial-fill or execution guarantee.",
                  quote_rule="penalty = worst_absolute_stress_pnl^2 / (2 * risk_scale_dollars); exact one-lot dealer inventory change",
                  status="indicative", inventory=position, current_stress_risk=risk)
    return result


def evaluate(snapshot: MarketSnapshot, vol_version: VolVersion, portfolio: tuple[Position, ...], settings: Settings) -> dict:
    """Compute a complete immutable-input bundle; callers publish it atomically."""
    from .volatility import vol_for_contract, validate_version

    # evaluate is a public entry point too, so it cannot rely only on the
    # controller having checked units, timestamps and slice conventions.
    if vol_version.nodes:
        invalid = validate_version(vol_version, snapshot)
        if invalid:
            raise ValueError("Invalid volatility version: " + "; ".join(invalid))

    portfolio = tuple(portfolio)
    positions = defaultdict(int)
    for position in portfolio:
        if position.contract_id not in snapshot.contract_map:
            raise ValueError(f"Unknown portfolio contract: {position.contract_id}")
        positions[position.contract_id] += position.quantity
    if settings.target_id and settings.target_id not in snapshot.contract_map:
        raise ValueError("Unknown target contract")
    identifiers = dict(snapshot_id=snapshot.snapshot_id, vol_version_id=vol_version.version_id,
                       portfolio_id=portfolio_id(portfolio), settings_id=settings.settings_id, engine_version=ENGINE_VERSION)
    result = dict(**identifiers, bundle_id=stable_id(identifiers, "bundle-"), as_of=snapshot.as_of,
                  mode=snapshot.mode, status="ok", warnings=[], prices=[], quotes=[], hedges={},
                  risk={}, scenario_assumptions=list(ASSUMPTIONS))
    warnings = result["warnings"]
    if not settings.fee_confirmed:
        warnings.append("Fees are user assumptions and have not been confirmed; results are research estimates.")
    if snapshot.mode != "live":
        warnings.append(f"{snapshot.mode}: valuation/stress analysis only; these are not live executable quotes.")
    scenarios = build_scenarios(settings)
    shocked = tuple(shocked_snapshot(snapshot, s) for s in scenarios)
    values, moves, vols, eligibility = {}, {}, {}, {}
    for contract in snapshot.contracts:
        row = dict(contract_id=contract.contract_id, kind=contract.kind, position=positions[contract.contract_id],
                   status="unavailable", warning="")
        observed = snapshot.quote_map.get(contract.contract_id)
        row.update(market_mid=None if observed is None else observed.mid,
                   market_bid=None if observed is None else observed.bid,
                   market_ask=None if observed is None else observed.ask,
                   observed_kind=None if observed is None else observed.kind)
        try:
            vol = 0.0 if contract.kind == "future" else vol_for_contract(contract, vol_version)
            value = instrument_value(contract, snapshot, vol)
            forward, tau = forward_for_contract(contract, snapshot), time_to_expiry(contract, snapshot)
            gs = greeks(contract.kind, forward, contract.strike, tau, vol, contract.right, snapshot.rate)
            pnl = np.array([(instrument_value(contract, market, scenario_vol(contract.kind, vol, s)) - value)
                            * contract.multiplier for s, market in zip(scenarios, shocked)])
            if not np.isfinite(pnl).all():
                raise ValueError("Non-finite fully repriced stress P&L")
            values[contract.contract_id], vols[contract.contract_id], moves[contract.contract_id] = value, vol, pnl
            eligibility[contract.contract_id] = (_freshness(contract, snapshot, settings)
                                                + _vol_freshness(contract, vol_version, snapshot, settings))
            if tau == 0:
                eligibility[contract.contract_id].append("Contract is expired")
            row.update(status="available", forward=forward, tau=tau, vol=vol, vol_unit=gs["vega_unit"],
                       model_price=value, model_minus_market=None if observed is None or observed.mid is None else value - observed.mid,
                       **gs, vega_bump_pnl_per_lot=gs["vega_bump_price_change"] * contract.multiplier,
                       warning="; ".join(eligibility[contract.contract_id]))
        except (ValueError, OverflowError) as exc:
            row["warning"] = str(exc)
            eligibility[contract.contract_id] = [str(exc)]
            warnings.append(f"{contract.contract_id}: {exc}")
        result["prices"].append(row)
    if any(quantity and identifier not in moves for identifier, quantity in positions.items()):
        result.update(status="incomplete")
        result["risk"] = dict(status="unavailable", scenarios=[], summary={}, reconciliation_error=None)
        warnings.append("Portfolio risk is unavailable because a held instrument cannot be fully repriced.")
        return result
    base = sum((quantity * moves[identifier] for identifier, quantity in positions.items() if quantity), np.zeros(len(scenarios)))
    held_reasons = [f"Held {identifier}: {reason}" for identifier, quantity in positions.items() if quantity
                    for reason in eligibility.get(identifier, [])]
    target_ids = [settings.target_id] if settings.target_id else [c.contract_id for c in snapshot.contracts if c.kind == "cso"][:1]
    for identifier in target_ids:
        contract = snapshot.contract_map[identifier]
        if contract.kind == "future":
            continue
        other_position_breaches = [f"Complete portfolio position limit cannot be restored by this quote: {other}={quantity}"
                                   for other, quantity in positions.items()
                                   if other != identifier and abs(quantity) > settings.position_limit]
        result["quotes"].append(_indicative_quote(contract, values.get(identifier), moves.get(identifier, np.zeros(len(scenarios))),
                                                  base, positions[identifier], snapshot, settings,
                                                  eligibility.get(identifier, ["Pricing unavailable"]) + held_reasons
                                                  + other_position_breaches))
    candidates = []
    if not held_reasons:
        for contract in snapshot.contracts:
            if contract.contract_id not in moves or eligibility[contract.contract_id]:
                continue
            if contract.kind == "cso" and not settings.allow_cso_hedge:
                continue
            if contract.kind not in ("future", "vanilla", "cso"):
                continue
            cost, source, bid_size, ask_size = _cost(contract, snapshot, settings)
            candidates.append(dict(contract=contract, pnl=moves[contract.contract_id], cost_per_lot=cost, cost_source=source,
                                   position=positions[contract.contract_id], bid_size=bid_size, ask_size=ask_size))
            if "assumed" in source:
                warnings.append(f"{contract.contract_id}: {source}")
    for name, selected in (("delta", [c for c in candidates if c["contract"].kind == "future"]), ("proxy", candidates)):
        hedge = optimize_hedge(base, selected, settings,
                               method="futures-only minimax" if name == "delta" else
                               ("futures + options minimax (CSO enabled)" if settings.allow_cso_hedge else "futures + vanilla minimax"),
                               initial_positions=dict(positions))
        if held_reasons:
            hedge.update(status="suppressed", message="; ".join(held_reasons))
        result["hedges"][name] = hedge
        if not hedge["position_constraints"]["compliant"]:
            warnings.append(f"{name}: complete portfolio has an unresolved position limit breach; no compliant hedge is available.")
    if not held_reasons:
        from .hedging import prefer_feasible_baseline
        result["hedges"]["proxy"] = prefer_feasible_baseline(
            result["hedges"]["proxy"], result["hedges"]["delta"], "futures", base, candidates, settings,
            initial_positions=dict(positions))
    scenario_rows, max_error = [], 0.0
    for i, scenario in enumerate(scenarios):
        contributions = {identifier: float(q * moves[identifier][i]) for identifier, q in positions.items() if q}
        row = dict(name=scenario.name, category=scenario.category, unhedged=float(base[i]), contributions=contributions,
                   parallel_dollars=scenario.parallel_dollars, twist_dollars=scenario.twist_dollars,
                   normal_vol_factor=scenario.normal_vol_factor, normal_vol_additive=scenario.normal_vol_additive,
                   lognormal_vol_factor=scenario.lognormal_vol_factor)
        max_error = max(max_error, abs(sum(contributions.values()) - base[i]))
        for name, hedge in result["hedges"].items():
            gross = base[i] + sum(t["quantity"] * moves[t["contract_id"]][i] for t in hedge["trades"])
            row[f"{name}_gross"] = float(gross)
            row[f"{name}_net"] = float(gross - hedge["cost"])
            max_error = max(max_error, abs(gross - hedge["gross_pnl"][i]), abs(gross - hedge["cost"] - hedge["net_pnl"][i]))
        scenario_rows.append(row)
    summary = {"unhedged": risk_summary(base)}
    summary.update({name: risk_summary(hedge["net_pnl"]) for name, hedge in result["hedges"].items()})
    result["risk"] = dict(status="stress_only", scenarios=scenario_rows, summary=summary,
                          reconciliation_error=float(max_error), label="Synthetic full-repricing stress scenarios; not historical VaR")
    time_guard = any(h.get("time_guard_triggered", False) for h in result["hedges"].values())
    result["reproducibility"] = dict(deterministic_scenarios=True, solver_node_limit=settings.hedge_node_limit,
                                     time_guard_triggered=time_guard, solver_work_cap_reached_before_time_guard=not time_guard,
                                     note="Time-guard incumbents may vary; identical inputs are covered by repeat tests when no time guard fires.")
    warnings.extend(held_reasons)
    result["warnings"] = list(dict.fromkeys(warnings))
    if held_reasons:
        result["status"] = "stale_inputs"
    elif snapshot.mode != "live" or not settings.fee_confirmed or any("assumed" in w for w in warnings):
        result["status"] = "analysis_only"
    return result
