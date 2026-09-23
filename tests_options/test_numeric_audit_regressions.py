"""Regression checks from the independent numerical self-audit."""
from dataclasses import replace

import numpy as np
import pytest

from options_lab.engine import evaluate
from options_lab.hedging import optimize_hedge
from options_lab.models import Position
from options_lab.pricing import instrument_value
from options_lab.scenarios import build_scenarios, scenario_vol, shocked_snapshot
from tests_options.test_engine import desk_fixture, hedge_problem


def test_nontradeable_held_position_breach_is_not_an_optimal_hedge():
    snapshot, version, _, settings = desk_fixture()
    settings = replace(settings, position_limit=40, allow_cso_hedge=False,
                       risk_limit_dollars=1e9, clip_size=10)
    portfolio = (Position(settings.target_id, -30), Position(settings.target_id, -20))
    result = evaluate(snapshot, version, portfolio, settings)
    for hedge in result["hedges"].values():
        assert hedge["status"] == "infeasible"
        assert hedge["trades"] == []
        report = hedge["position_constraints"]
        assert report["scope"] == "full_portfolio"
        assert report["initial_breaches"] == {settings.target_id: -50}
        assert report["final_breaches"] == {settings.target_id: -50}
        assert report["compliant"] is False
    assert any("position limit" in warning.lower() for warning in result["warnings"])


def test_no_candidates_still_checks_all_held_position_limits():
    _, _, _, settings = desk_fixture()
    result = optimize_hedge(np.array([-100., 0., 100.]), [], settings,
                            initial_positions={"untradeable-CSO": -41})
    assert result["status"] == "infeasible"
    assert result["position_constraints"]["final_breaches"] == {"untradeable-CSO": -41}


def test_tradable_initial_breach_can_be_fully_repaired_and_audited():
    pnl, candidates, settings = hedge_problem()
    identifier = candidates[0]["contract"].contract_id
    candidates = [dict(candidates[0], position=-6)]
    settings = replace(settings, position_limit=4)
    result = optimize_hedge(pnl, candidates, settings, initial_positions={identifier: -6, "held-CSO": -2})
    assert result["status"] == "optimal"
    assert result["position_constraints"]["initial_breaches"] == {identifier: -6}
    assert result["position_constraints"]["final_breaches"] == {}
    assert result["position_constraints"]["final_positions"][identifier] == -4
    assert result["position_constraints"]["final_positions"]["held-CSO"] == -2
    assert result["position_constraints"]["compliant"] is True


def test_quote_search_can_reach_a_fully_compliant_clip_after_illegal_small_clips():
    snapshot, version, _, settings = desk_fixture()
    settings = replace(settings, position_limit=40, allow_cso_hedge=False,
                       risk_limit_dollars=1e9, clip_size=10)
    portfolio = (Position(settings.target_id, -50),)
    quote = evaluate(snapshot, version, portfolio, settings)["quotes"][0]
    assert quote["bid_size"] == 10
    assert quote["ask_size"] == 0
    assert "final position" in quote["position_policy"].lower()
    assert quote["initial_position_breach"] is True
    # Merely reducing the breach is not presented as a compliant filled clip.
    partial = evaluate(snapshot, version, portfolio, replace(settings, clip_size=9))["quotes"][0]
    assert partial["bid_size"] == 0


def test_quote_cannot_ignore_an_over_limit_position_in_another_contract():
    snapshot, version, _, settings = desk_fixture()
    cso_id = settings.target_id
    vanilla_id = next(c.contract_id for c in snapshot.contracts if c.kind == "vanilla")
    settings = replace(settings, target_id=vanilla_id, risk_limit_dollars=1e9)
    quote = evaluate(snapshot, version, (Position(cso_id, -50),), settings)["quotes"][0]
    assert quote["status"] == "suppressed"
    assert quote["bid_size"] == quote["ask_size"] == 0
    assert any("complete portfolio" in reason.lower() for reason in quote["reasons"])


@pytest.mark.parametrize("count", [7, 8, 15, 100, 1000])
def test_absolute_normal_vol_stress_exists_at_zero_without_shocking_vanilla_vol(count):
    _, _, _, settings = desk_fixture()
    scenarios = build_scenarios(replace(settings, scenario_count=count))
    assert len(scenarios) == count
    assert len({s.name for s in scenarios}) == count
    absolute = next(s for s in scenarios if s.name == "cso_basis_vol_up_1_normal")
    assert absolute.normal_vol_additive == 1.0
    assert scenario_vol("cso", 0.0, absolute) == 1.0
    assert scenario_vol("vanilla", 0.30, absolute) == 0.30
    assert any(s.name == "cso_basis_vol_up_50pct" for s in scenarios)


def test_zero_normal_vol_absolute_stress_reports_nonzero_fully_repriced_loss():
    snapshot, version, portfolio, settings = desk_fixture()
    version = replace(version, nodes=tuple(replace(n, value=0.0) if n.model == "normal" else n for n in version.nodes))
    result = evaluate(snapshot, version, portfolio, replace(settings, scenario_count=7))
    absolute = next(s for s in build_scenarios(replace(settings, scenario_count=7)) if s.name == "cso_basis_vol_up_1_normal")
    row = next(s for s in result["risk"]["scenarios"] if s["name"] == absolute.name)
    contract = snapshot.contract_map[settings.target_id]
    expected = portfolio[0].quantity * contract.multiplier * (
        instrument_value(contract, shocked_snapshot(snapshot, absolute), 1.0)
        - instrument_value(contract, snapshot, 0.0))
    assert expected < -500.0
    assert row["unhedged"] == pytest.approx(expected, abs=0.01)
    assert row["normal_vol_additive"] == 1.0
