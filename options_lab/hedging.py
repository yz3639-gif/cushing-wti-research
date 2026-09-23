"""Bounded integer scenario hedges. Suggestions only; no order interfaces."""
from __future__ import annotations

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .models import Settings
from .scenarios import risk_summary


def _initial_positions(candidates, initial_positions):
    """Use the caller's complete book when supplied, including non-candidates."""
    identifiers = [c["contract"].contract_id for c in candidates]
    if len(set(identifiers)) != len(identifiers):
        raise ValueError("Duplicate hedge candidate identity")
    positions = dict(initial_positions) if initial_positions is not None else {
        c["contract"].contract_id: c.get("position", 0) for c in candidates}
    for candidate in candidates:
        identifier = candidate["contract"].contract_id
        current = positions.setdefault(identifier, 0)
        if "position" in candidate and candidate["position"] != current:
            raise ValueError("Candidate position differs from the complete initial portfolio")
    if any(isinstance(q, bool) or not isinstance(q, int) for q in positions.values()):
        raise ValueError("Initial positions require integer lots")
    return positions, "full_portfolio" if initial_positions is not None else "candidate_positions_only"


def _position_report(initial, changes, settings, scope):
    final = dict(initial)
    for identifier, quantity in changes.items():
        final[identifier] = final.get(identifier, 0) + int(quantity)
    initial_breaches = {k: v for k, v in initial.items() if abs(v) > settings.position_limit}
    final_breaches = {k: v for k, v in final.items() if abs(v) > settings.position_limit}
    return dict(scope=scope, limit=settings.position_limit, initial_positions=dict(initial),
                initial_breaches=initial_breaches, final_positions=final,
                final_breaches=final_breaches, compliant=not final_breaches)


def optimize_hedge(base_pnl, candidates: list[dict], settings: Settings, *, method: str = "proxy",
                   initial_positions: dict[str, int] | None = None) -> dict:
    """Minimize max(abs(stress P&L)) + crossing/fee cost, with integer lots.

The t + cost risk constraint is conservative for absolute net scenario P&L.
No distributional probabilities, estimated beta, or future execution guarantee
are attached to this optimizer. A nonoptimal incumbent is never called optimal.
"""
    baseline = np.asarray(base_pnl, dtype=float)
    if baseline.ndim != 1 or not np.isfinite(baseline).all():
        raise ValueError("A finite one-dimensional scenario P&L is required")
    original = risk_summary(baseline)
    result = dict(status="no_candidates", method=method, trades=[], cost=0.0,
                  gross_pnl=baseline.tolist(), net_pnl=baseline.tolist(),
                  objective=original["worst_abs_pnl"], message="No eligible hedge instruments",
                  termination_reason="not_run", time_guard_triggered=False, **original)
    positions, position_scope = _initial_positions(candidates, initial_positions)
    result["position_constraints"] = _position_report(positions, {}, settings, position_scope)
    allowed_ids = {c["contract"].contract_id for c in candidates}
    fixed_breaches = {k: q for k, q in positions.items() if k not in allowed_ids and abs(q) > settings.position_limit}
    if fixed_breaches:
        result.update(status="infeasible", termination_reason="position_limit",
                      message="Position limit cannot be restored for non-tradeable held instruments: "
                              + ", ".join(f"{k}={q}" for k, q in fixed_breaches.items()))
        return result
    if not candidates:
        return result
    n = len(candidates)
    moves = np.column_stack([np.asarray(c["pnl"], dtype=float) for c in candidates])
    costs = np.asarray([c["cost_per_lot"] for c in candidates], dtype=float)
    if moves.shape != (len(baseline), n) or not np.isfinite(moves).all() or not np.isfinite(costs).all() or (costs < 0).any():
        raise ValueError("Invalid hedge scenario or cost matrix")
    lower, upper = [], []
    for candidate in candidates:
        contract = candidate["contract"]
        bound = settings.future_bound if contract.kind == "future" else settings.option_bound
        current = positions[contract.contract_id]
        low = max(-bound, -settings.position_limit - current)
        high = min(bound, settings.position_limit - current)
        # Where available, displayed BBO size limits this suggestion; it is not a fill prediction.
        if candidate.get("bid_size") is not None:
            low = max(low, -int(candidate["bid_size"]))
        if candidate.get("ask_size") is not None:
            high = min(high, int(candidate["ask_size"]))
        lower.append(low)
        upper.append(high)
    if any(a > b for a, b in zip(lower, upper)):
        result.update(status="infeasible", termination_reason="position_limit",
                      message="Position limits cannot be met within trade bounds")
        return result
    zero_feasible = (all(lo <= 0 <= hi for lo, hi in zip(lower, upper))
                     and original["worst_abs_pnl"] <= settings.risk_limit_dollars + 1e-8)
    result["zero_trade_feasible"] = zero_feasible
    # Variables: signed integer trades h, continuous |h| auxiliaries a, worst gross |P&L| t.
    objective = np.r_[np.zeros(n), costs + 1e-8, 1.0]
    rows, lhs, rhs = [], [], []
    for s, pnl in enumerate(baseline):
        rows.append(np.r_[moves[s], np.zeros(n), -1.0]); lhs.append(-np.inf); rhs.append(-pnl)
        rows.append(np.r_[-moves[s], np.zeros(n), -1.0]); lhs.append(-np.inf); rhs.append(pnl)
    for j in range(n):
        row = np.zeros(2 * n + 1); row[j] = 1; row[n + j] = -1
        rows.append(row); lhs.append(-np.inf); rhs.append(0)
        row = np.zeros(2 * n + 1); row[j] = -1; row[n + j] = -1
        rows.append(row); lhs.append(-np.inf); rhs.append(0)
    rows.append(np.r_[np.zeros(n), np.ones(n), 0.0]); lhs.append(-np.inf); rhs.append(settings.gross_lot_limit)
    rows.append(np.r_[np.zeros(n), costs, 1.0]); lhs.append(-np.inf); rhs.append(settings.risk_limit_dollars)
    constraints = LinearConstraint(np.asarray(rows), np.asarray(lhs), np.asarray(rhs))
    bounds = Bounds(np.r_[lower, np.zeros(n), 0.0], np.r_[upper, np.full(n, settings.gross_lot_limit), np.inf])
    try:
        solution = milp(objective, integrality=np.r_[np.ones(n), np.zeros(n + 1)], bounds=bounds,
                        constraints=constraints,
                        options={"time_limit": settings.hedge_time_limit_seconds,
                                 "node_limit": settings.hedge_node_limit, "mip_rel_gap": 1e-8})
    except (ValueError, RuntimeError) as exc:
        result.update(status="solver_error", message=str(exc))
        return result
    result["message"] = str(solution.message)
    result["solver_status"] = int(solution.status)
    gap = getattr(solution, "mip_gap", None)
    result["mip_gap"] = None if gap is None or not np.isfinite(gap) else float(gap)
    time_guard = "time limit" in str(solution.message).lower()
    result["time_guard_triggered"] = time_guard
    result["termination_reason"] = ("time_limit" if time_guard else
                                    {0: "optimal", 1: "node_or_iteration_limit", 2: "infeasible", 3: "unbounded"}.get(solution.status, "solver_error"))
    result["reproducibility_note"] = ("Time guard interrupted search; incumbent may vary across runs" if time_guard else
                                       "Fixed scenario grid and node limit; no randomized scenario sampling")
    if solution.x is None:
        result["status"] = {1: "time_limit_no_solution" if time_guard else "limit_no_solution", 2: "infeasible", 3: "unbounded"}.get(solution.status, "solver_error")
        if solution.status == 1 and zero_feasible:
            result.update(status="fallback_zero", message="Search limit without an incumbent; retained independently feasible zero-trade baseline. " + result["message"])
        return result
    trades = np.rint(solution.x[:n]).astype(int)
    gross = baseline + moves @ trades
    cost = float(costs @ np.abs(trades))
    position_report = _position_report(positions, {c["contract"].contract_id: int(h) for c, h in zip(candidates, trades)},
                                       settings, position_scope)
    # Validate the rounded incumbent ourselves before presenting it as feasible.
    feasible = (np.max(np.abs(solution.x[:n] - trades)) < 1e-5
                and all(lo <= h <= hi for h, lo, hi in zip(trades, lower, upper))
                and np.abs(trades).sum() <= settings.gross_lot_limit
                and np.max(np.abs(gross)) + cost <= settings.risk_limit_dollars + 1e-5
                and position_report["compliant"])
    if not feasible:
        result.update(status="invalid_incumbent", message="Rounded solver incumbent failed independent constraints")
        return result
    net = gross - cost
    if zero_feasible and np.any(trades) and np.max(np.abs(gross)) + cost >= original["worst_abs_pnl"] - 1e-8:
        result.update(status="fallback_zero", message="Solver incumbent did not improve the feasible zero-trade objective; retained zero trades. " + result["message"],
                      discarded_incumbent_objective=float(np.max(np.abs(gross)) + cost))
        return result
    details = []
    for candidate, h in zip(candidates, trades):
        if h:
            details.append(dict(contract_id=candidate["contract"].contract_id, quantity=int(h),
                                direction="buy" if h > 0 else "sell", lots=int(abs(h)),
                                cost_per_lot=float(candidate["cost_per_lot"]), total_cost=float(abs(h) * candidate["cost_per_lot"]),
                                cost_source=candidate.get("cost_source", "explicit assumption")))
    result.update(status="optimal" if solution.status == 0 else "feasible_limit", trades=details,
                  cost=cost, gross_pnl=gross.tolist(), net_pnl=net.tolist(),
                  objective=float(np.max(np.abs(gross)) + cost), position_constraints=position_report,
                  **risk_summary(net))
    return result


def prefer_feasible_baseline(result, baseline, label, base_pnl, candidates, settings, *, initial_positions=None):
    """A limited search must not replace a better, independently legal baseline."""
    if not candidates or baseline.get("status") in ("suppressed", "infeasible", "solver_error", "invalid_incumbent"):
        return result
    proposed = {t["contract_id"]: t["quantity"] for t in baseline["trades"]}
    positions, position_scope = _initial_positions(candidates, initial_positions)
    position_report = _position_report(positions, proposed, settings, position_scope)
    if not position_report["compliant"]:
        return result
    allowed = {c["contract"].contract_id for c in candidates}
    if set(proposed) - allowed:
        return result
    gross = np.asarray(base_pnl, dtype=float).copy()
    cost, lots, trades = 0.0, 0, []
    for candidate in candidates:
        contract = candidate["contract"]
        h = proposed.get(contract.contract_id, 0)
        bound = settings.future_bound if contract.kind == "future" else settings.option_bound
        if (not isinstance(h, int) or abs(h) > bound
                or abs(positions[contract.contract_id] + h) > settings.position_limit
                or (h < 0 and candidate.get("bid_size") is not None and -h > int(candidate["bid_size"]))
                or (h > 0 and candidate.get("ask_size") is not None and h > int(candidate["ask_size"]))):
            return result
        gross += h * np.asarray(candidate["pnl"], dtype=float)
        leg_cost = abs(h) * candidate["cost_per_lot"]
        cost += leg_cost; lots += abs(h)
        if h:
            trades.append(dict(contract_id=contract.contract_id, quantity=h, lots=abs(h), direction="buy" if h > 0 else "sell",
                               cost_per_lot=float(candidate["cost_per_lot"]), total_cost=float(leg_cost), cost_source=candidate.get("cost_source", "explicit assumption")))
    objective = float(np.max(np.abs(gross)) + cost)
    if lots > settings.gross_lot_limit or objective > settings.risk_limit_dollars + 1e-8:
        return result
    if objective >= result["objective"] - 1e-8:
        return result
    updated = dict(result)
    updated["search_result"] = {key: result.get(key) for key in ("status", "message", "objective", "termination_reason", "mip_gap", "time_guard_triggered")}
    updated.update(status=f"fallback_{label}", trades=trades, cost=float(cost), objective=objective,
                   gross_pnl=gross.tolist(), net_pnl=(gross - cost).tolist(), mip_gap=None,
                   position_constraints=position_report,
                   time_guard_triggered=result.get("time_guard_triggered", False) or baseline.get("time_guard_triggered", False),
                   message=f"Retained independently checked {label} baseline with a lower stress-plus-cost objective; no optimality claim.",
                   **risk_summary(gross - cost))
    return updated
