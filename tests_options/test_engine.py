"""Constraint, provenance, inventory sign and full-repricing reconciliation tests."""
from dataclasses import replace
from types import SimpleNamespace
import json

import numpy as np
import pytest

from options_lab.engine import evaluate
from options_lab.hedging import optimize_hedge, prefer_feasible_baseline
from options_lab.models import Contract, Quote, MarketSnapshot, VolNode, VolVersion, Position, Settings, NORMAL_UNIT, LOGNORMAL_UNIT
from options_lab.pricing import instrument_value
from options_lab.scenarios import build_scenarios, shocked_snapshot


def desk_fixture():
    at, expiry = "2025-07-01T18:00:00+00:00", "2025-08-18T18:00:00+00:00"
    front = Contract("CLQ5", "CL", "future", (), "2025-08-20T18:00:00+00:00")
    back = Contract("CLU5", "CL", "future", (), "2025-09-20T18:00:00+00:00")
    cso = Contract("7A-Q5-C1", "7A", "cso", (front.contract_id, back.contract_id), expiry, 1., "call")
    vanilla1 = Contract("LC-Q5-C75", "LC", "vanilla", (front.contract_id,), expiry, 75., "call")
    vanilla2 = Contract("LC-U5-C74", "LC", "vanilla", (back.contract_id,), expiry, 74., "call")
    contracts = (front, back, cso, vanilla1, vanilla2)
    qs = [Quote(front.contract_id, at, 74.99, 75.01, 100, 100, source="engineering fixture"),
          Quote(back.contract_id, at, 73.99, 74.01, 100, 100, source="engineering fixture")]
    temporary = MarketSnapshot(contracts, tuple(qs), at, "engineering_fixture", "engineering fixture")
    nodes = []
    for c in contracts[2:]:
        vol = 3.0 if c.kind == "cso" else .3
        model_value = instrument_value(c, temporary, vol)
        qs.append(Quote(c.contract_id, at, model_value - .01, model_value + .01, 50, 50, source="engineering fixture"))
        nodes.append(VolNode(c.product, c.underlyings, c.expiry, c.strike, c.model, vol,
                             NORMAL_UNIT if c.kind == "cso" else LOGNORMAL_UNIT, at, "engineering fixture"))
    return replace(temporary, quotes=tuple(qs)), VolVersion(tuple(nodes), created_at=at), (Position(cso.contract_id, -4),), Settings(target_id=cso.contract_id, scenario_count=15, fee_per_contract=1., fee_confirmed=True)


def test_full_repricing_reconciles_gross_cost_net_and_carries_versions():
    snapshot, version, portfolio, settings = desk_fixture()
    bundle = evaluate(snapshot, version, portfolio, settings)
    assert bundle["snapshot_id"] == snapshot.snapshot_id
    assert bundle["vol_version_id"] == version.version_id
    assert bundle["risk"]["reconciliation_error"] < 1e-8
    assert len(bundle["risk"]["scenarios"]) == settings.scenario_count
    assert bundle["status"] == "analysis_only"
    json.dumps(bundle, allow_nan=False)
    for row in bundle["risk"]["scenarios"]:
        assert row["unhedged"] == pytest.approx(sum(row["contributions"].values()))
        for method in ("delta", "proxy"):
            hedge = bundle["hedges"][method]
            assert row[f"{method}_net"] == pytest.approx(row[f"{method}_gross"] - hedge["cost"])
            assert all(isinstance(t["quantity"], int) for t in hedge["trades"])
            assert sum(abs(t["quantity"]) for t in hedge["trades"]) <= settings.gross_lot_limit
    assert all(snapshot.contract_map[t["contract_id"]].kind == "future" for t in bundle["hedges"]["delta"]["trades"])
    assert not any(t["contract_id"] == settings.target_id for t in bundle["hedges"]["proxy"]["trades"])


def test_quote_inventory_skew_has_correct_dealer_buy_sell_signs():
    snapshot, version, _, settings = desk_fixture()
    settings = replace(settings, horizon_days=0, risk_limit_dollars=1e9)
    flat = evaluate(snapshot, version, (), settings)["quotes"][0]
    long = evaluate(snapshot, version, (Position(settings.target_id, 5),), settings)["quotes"][0]
    short = evaluate(snapshot, version, (Position(settings.target_id, -5),), settings)["quotes"][0]
    assert long["bid"] <= flat["bid"] <= short["bid"]
    assert long["ask"] <= flat["ask"] <= short["ask"]
    for q in (flat, long, short):
        assert 0 <= q["bid"] < q["ask"]
        assert q["bid"] / .01 == pytest.approx(round(q["bid"] / .01))


def test_actual_risk_and_position_constraints_change_displayed_size():
    snapshot, version, _, settings = desk_fixture()
    bundle = evaluate(snapshot, version, (Position(settings.target_id, 4),), replace(settings, position_limit=4, clip_size=3, risk_limit_dollars=1e9))
    assert bundle["quotes"][0]["bid_size"] == 0
    assert bundle["quotes"][0]["ask_size"] > 0


def test_stale_or_disconnected_held_inputs_suppress_new_proposals():
    snapshot, version, portfolio, settings = desk_fixture()
    stale = replace(snapshot, quotes=tuple(replace(q, as_of="2025-07-01T17:00:00+00:00") if q.contract_id == "CLQ5" else q for q in snapshot.quotes))
    result = evaluate(stale, version, portfolio, settings)
    assert result["status"] == "stale_inputs"
    assert result["quotes"][0]["bid"] is None
    assert result["hedges"]["proxy"]["status"] == "suppressed"
    result = evaluate(replace(snapshot, feed_alive=False), version, portfolio, settings)
    assert result["quotes"][0]["status"] == "suppressed"


def test_flagged_and_expired_futures_cannot_be_recommended():
    snapshot, version, portfolio, settings = desk_fixture()
    flagged = replace(snapshot, quotes=tuple(replace(q, flags=("invalid source record",)) if q.contract_id == "CLQ5" else q for q in snapshot.quotes))
    result = evaluate(flagged, version, portfolio, settings)
    assert result["hedges"]["proxy"]["status"] == "suppressed"
    assert result["quotes"][0]["status"] == "suppressed"
    expired = replace(snapshot, contracts=tuple(replace(c, expiry=snapshot.as_of) if c.contract_id == "CLQ5" else c for c in snapshot.contracts))
    result = evaluate(expired, version, (), settings)
    assert all(t["contract_id"] != "CLQ5" for h in result["hedges"].values() for t in h["trades"])


def test_eod_preserves_real_mark_and_explicitly_assumes_cost_without_inventing_bbo():
    snapshot, version, portfolio, settings = desk_fixture()
    snapshot = replace(snapshot, mode="observed_eod", quotes=tuple(replace(q, bid=None, ask=None, bid_size=None, ask_size=None, mark=q.mid, kind="settlement") for q in snapshot.quotes))
    bundle = evaluate(snapshot, version, portfolio, replace(settings, fee_confirmed=False))
    assert bundle["status"] == "analysis_only"
    assert all(row["market_bid"] is None and row["market_ask"] is None for row in bundle["prices"])
    assert bundle["quotes"][0]["status"] == "indicative"
    assert any("one tick" in w for w in bundle["warnings"])
    assert bundle["hedges"]["proxy"]["status"] in ("optimal", "feasible_limit")


def test_missing_held_vol_fails_closed_and_edit_changes_complete_bundle():
    snapshot, version, portfolio, settings = desk_fixture()
    missing = replace(version, nodes=tuple(n for n in version.nodes if n.product != "7A"))
    assert evaluate(snapshot, missing, portfolio, settings)["status"] == "incomplete"
    original = evaluate(snapshot, version, portfolio, settings)
    edited = replace(version, nodes=tuple(replace(n, value=n.value * 1.2) for n in version.nodes))
    changed = evaluate(snapshot, edited, portfolio, settings)
    assert changed["bundle_id"] != original["bundle_id"]
    assert changed["quotes"][0]["model_price"] > original["quotes"][0]["model_price"]
    assert changed["risk"]["summary"]["unhedged"] != original["risk"]["summary"]["unhedged"]


def test_direct_engine_rejects_future_or_wrong_unit_nodes_but_handles_empty_coverage():
    snapshot, version, portfolio, settings = desk_fixture()
    for change in ({"as_of": "2026-01-01T00:00:00+00:00"}, {"unit": "percentage_points"}, {"value": -1.}):
        broken = replace(version, nodes=(replace(version.nodes[0], **change),) + version.nodes[1:])
        with pytest.raises(ValueError, match="Invalid volatility version"):
            evaluate(snapshot, broken, portfolio, settings)
    assert evaluate(snapshot, replace(version, nodes=()), portfolio, settings)["status"] == "incomplete"


def test_stale_otm_calibration_cannot_hide_behind_fresh_opposite_right_quote():
    from options_lab.volatility import calibrate_market
    snapshot, _, _, settings = desk_fixture()
    call = snapshot.contract_map[settings.target_id]
    put = replace(call, contract_id="7A-Q5-P1", right="put")
    call_quote = snapshot.quote_map[call.contract_id]
    put_quote = replace(call_quote, contract_id=put.contract_id)
    snapshot = replace(snapshot, contracts=snapshot.contracts + (put,), quotes=tuple(
        replace(q, as_of="2025-07-01T17:00:00+00:00") if q.contract_id == call.contract_id else q for q in snapshot.quotes) + (put_quote,))
    version = calibrate_market(snapshot)
    settings = replace(settings, target_id=put.contract_id)
    bundle = evaluate(snapshot, version, (Position(put.contract_id, 2),), settings)
    assert bundle["status"] == "stale_inputs"
    assert bundle["quotes"][0]["status"] == "suppressed"
    assert any("Stale calibrated volatility" in reason for reason in bundle["quotes"][0]["reasons"])
    # An explicitly pinned manual version is a different, disclosed choice.
    manual = evaluate(snapshot, replace(version, origin="manual"), (Position(put.contract_id, 2),), settings)
    assert manual["quotes"][0]["status"] == "indicative"


def test_negative_option_bbo_suppresses_without_rejecting_negative_spread():
    snapshot, version, portfolio, settings = desk_fixture()
    invalid = replace(snapshot, quotes=tuple(replace(q, bid=-.01, ask=.02) if q.contract_id == settings.target_id else q for q in snapshot.quotes))
    bundle = evaluate(invalid, version, portfolio, settings)
    assert bundle["quotes"][0]["status"] == "suppressed"
    assert any("Negative observed option" in reason for reason in bundle["quotes"][0]["reasons"])


def test_scenario_common_factor_is_explicit_and_does_not_invent_correlation():
    snapshot, _, _, settings = desk_fixture()
    scenarios = build_scenarios(settings)
    basis = next(s for s in scenarios if s.category == "basis")
    assert basis.normal_vol_factor == 1.5 and basis.lognormal_vol_factor == 1.0
    common = next(s for s in scenarios if s.category == "common_vol")
    assert common.normal_vol_factor == common.lognormal_vol_factor
    shocked = shocked_snapshot(snapshot, next(s for s in scenarios if s.name == "curve_steepens"))
    assert shocked.quote_map["CLQ5"].mid - shocked.quote_map["CLU5"].mid == pytest.approx(3.)


@pytest.mark.parametrize("count", [7, 8, 82, 100, 1000])
def test_exact_requested_scenario_count_and_unique_names(count):
    scenarios = build_scenarios(Settings(scenario_count=count))
    assert len(scenarios) == count
    assert len({s.name for s in scenarios}) == count


def hedge_problem(cost=5.):
    snapshot, _, _, settings = desk_fixture()
    candidates = [dict(contract=snapshot.contracts[0], pnl=np.array([-50., 0., 50.]), cost_per_lot=cost, position=0)]
    return np.array([-100., 0., 100.]), candidates, replace(settings, future_bound=5, risk_limit_dollars=1000., scenario_count=7)


def test_integer_minimax_has_known_solution_and_cost_changes_decision():
    pnl, candidates, settings = hedge_problem()
    result = optimize_hedge(pnl, candidates, settings)
    assert result["status"] == "optimal"
    assert result["trades"][0]["quantity"] == -2
    assert result["trades"][0]["direction"] == "sell"
    assert result["trades"][0]["lots"] == 2
    assert result["cost"] == 10.
    assert result["net_pnl"] == [-10., -10., -10.]
    pnl, candidates, settings = hedge_problem(60.)
    assert optimize_hedge(pnl, candidates, settings)["trades"] == []


def test_minimax_enforces_hard_risk_gross_and_displayed_lot_bounds():
    pnl, candidates, settings = hedge_problem()
    result = optimize_hedge(pnl, candidates, replace(settings, gross_lot_limit=1))
    assert result["trades"][0]["quantity"] == -1
    limited = [dict(candidates[0], bid_size=1, ask_size=100)]
    assert optimize_hedge(pnl, limited, settings)["trades"][0]["quantity"] == -1
    assert optimize_hedge(pnl, limited, replace(settings, risk_limit_dollars=20.))["status"] == "infeasible"


def test_time_limit_incumbent_is_not_mislabeled_optimal(monkeypatch):
    pnl, candidates, settings = hedge_problem()
    monkeypatch.setattr("options_lab.hedging.milp", lambda *a, **k: SimpleNamespace(x=np.array([-2., 2., 0.]), status=1, message="Time limit", mip_gap=.2))
    limited = optimize_hedge(pnl, candidates, settings)
    assert limited["status"] == "feasible_limit" and limited["time_guard_triggered"]
    monkeypatch.setattr("options_lab.hedging.milp", lambda *a, **k: SimpleNamespace(x=None, status=1, message="Time limit", mip_gap=None))
    assert optimize_hedge(pnl, candidates, settings)["status"] == "fallback_zero"
    assert optimize_hedge(pnl, candidates, replace(settings, risk_limit_dollars=20.))["status"] == "time_limit_no_solution"


def test_dominated_limited_incumbent_falls_back_to_legal_zero(monkeypatch):
    pnl, candidates, settings = hedge_problem()
    monkeypatch.setattr("options_lab.hedging.milp", lambda *a, **k: SimpleNamespace(x=np.array([1., 1., 150.]), status=1, message="Iteration limit", mip_gap=.8))
    result = optimize_hedge(pnl, candidates, settings)
    assert result["status"] == "fallback_zero"
    assert result["trades"] == [] and result["objective"] == 100.
    assert result["discarded_incumbent_objective"] == 155.
    assert not result["time_guard_triggered"]


def test_futures_baseline_is_independently_checked_before_proxy_fallback(monkeypatch):
    pnl, candidates, settings = hedge_problem()
    baseline = optimize_hedge(pnl, candidates, settings)
    monkeypatch.setattr("options_lab.hedging.milp", lambda *a, **k: SimpleNamespace(x=np.array([0., 0., 100.]), status=1, message="Iteration limit", mip_gap=.8))
    limited = optimize_hedge(pnl, candidates, settings)
    result = prefer_feasible_baseline(limited, baseline, "futures", pnl, candidates, settings)
    assert result["status"] == "fallback_futures"
    assert result["net_pnl"] == [-10., -10., -10.]
    assert result["search_result"]["status"] == "feasible_limit"
    assert result["trades"][0]["quantity"] == -2
    assert prefer_feasible_baseline(limited, baseline, "futures", pnl, [dict(candidates[0], bid_size=1)], settings) == limited


def test_identical_inputs_reproduce_bundle_without_a_time_guard():
    snapshot, version, portfolio, settings = desk_fixture()
    settings = replace(settings, hedge_time_limit_seconds=10.)
    first = evaluate(snapshot, version, portfolio, settings)
    for _ in range(3):
        repeat = evaluate(snapshot, version, portfolio, settings)
        assert not any(h["time_guard_triggered"] for h in repeat["hedges"].values())
        assert repeat == first
