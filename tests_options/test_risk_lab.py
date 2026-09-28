"""Independent fixed-ticket reconciliation and export failure gates."""
from copy import deepcopy
from dataclasses import replace
import json
import math

import pytest

from options_lab import risk_lab
from options_lab.scenarios import Scenario


@pytest.fixture(scope="module")
def evidence():
    import options_lab.hedging

    def no_optimizer(*args, **kwargs):
        raise AssertionError("Risk Lab must never re-optimize a saved ticket")

    original_hash = risk_lab.sha256(risk_lab.ROOT / risk_lab.DEMO)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(options_lab.hedging, "optimize_hedge", no_optimizer)
        artifact = risk_lab.build_sidecar()
    assert risk_lab.sha256(risk_lab.ROOT / risk_lab.DEMO) == original_hash
    demo, source, settings = risk_lab._inputs(risk_lab.ROOT)
    return artifact, demo, source, settings


def test_all_45_cases_reproduce_saved_tickets_without_optimizer(evidence):
    artifact, _, _, _ = evidence
    report = artifact["validation"]
    assert report["passed"] and report["repriced"]
    assert report["case_count"] == 45
    assert report["optimization_scenarios"] == 100
    assert report["challenge_scenarios"] == 12
    assert report["reconciliation_checks"] > 100_000
    assert max(report["maximum_errors_usd"].values()) < .01
    assert artifact["meta"]["optimizer_rerun"] is False


def test_published_sidecar_reprices_and_matches_current_inputs(evidence):
    artifact = json.loads((risk_lab.ROOT / risk_lab.OUTPUT).read_text())
    assert risk_lab.validate_sidecar(artifact)["passed"]


def test_challenges_are_new_fixed_one_day_shocks(evidence):
    artifact, _, _, _ = evidence
    fields = ("parallel_dollars", "twist_dollars", "normal_vol_factor",
              "lognormal_vol_factor", "normal_vol_additive", "elapsed_days")
    signatures = lambda key: {tuple(s[f] for f in fields) for s in artifact["scenario_sets"][key]}
    assert len(signatures("challenge")) == 12
    assert not signatures("optimization") & signatures("challenge")
    assert all(s["elapsed_days"] == 1 for s in artifact["scenario_sets"]["challenge"])


def test_futures_challenge_pnl_matches_direct_contract_arithmetic(evidence):
    artifact, demo, _, _ = evidence
    for case in artifact["cases"]:
        snapshot = demo["snapshots"][case["snapshot_index"]]
        futures = sorted((c for c in snapshot["contracts"] if c["kind"] == "future"),
                         key=lambda c: (c["expiry"], c["contract_id"]))
        assert len(futures) == 2
        for contract, coordinate in zip(futures, (.5, -.5)):
            for i, scenario in enumerate(artifact["scenario_sets"]["challenge"]):
                expected = (scenario["parallel_dollars"] + coordinate * scenario["twist_dollars"]) * contract["multiplier"]
                assert case["unit_pnl"]["challenge"][contract["contract_id"]][i] == pytest.approx(expected, abs=1e-8)


def test_omission_is_at_inception_and_costs_are_removed_once(evidence):
    artifact, demo, _, _ = evidence
    case, original = artifact["cases"][0], demo["cases"][0]
    snapshot = demo["snapshots"][0]
    trades = original["bundle"]["hedges"]["proxy"]["trades"]
    whole = risk_lab.fixed_ticket(case, original, snapshot)
    for trade in trades:
        omitted = risk_lab.fixed_ticket(case, original, snapshot, excluded=[trade["contract_id"]])
        assert omitted["cost"] == pytest.approx(whole["cost"] - trade["total_cost"])
        for i, pnl in enumerate(omitted["net_pnl"]):
            expected = whole["net_pnl"][i] - trade["quantity"] * case["unit_pnl"]["optimization"][trade["contract_id"]][i] + trade["total_cost"]
            assert pnl == pytest.approx(expected, abs=1e-8)
    empty = risk_lab.fixed_ticket(case, original, snapshot, excluded=[t["contract_id"] for t in trades])
    assert empty["cost"] == empty["gross_lots"] == 0
    assert empty["net_pnl"] == empty["base_pnl"]
    assert "status" not in empty  # Original solver feasibility is not inherited.


def test_cost_stress_recomputes_constraint_and_preserves_quantities(evidence):
    artifact, demo, _, _ = evidence
    case, original, snapshot = artifact["cases"][0], demo["cases"][0], demo["snapshots"][0]
    base = risk_lab.fixed_ticket(case, original, snapshot)
    doubled = risk_lab.fixed_ticket(case, original, snapshot, cost_scale=2)
    assert doubled["cost"] == 2 * base["cost"]
    assert doubled["gross_pnl"] == base["gross_pnl"]
    assert doubled["final_positions"] == base["final_positions"]
    assert doubled["worst_loss"] >= base["worst_loss"]
    expensive = risk_lab.fixed_ticket(case, original, snapshot, cost_scale=1e6)
    assert not expensive["risk_compliant"]
    assert expensive["position_compliant"]


@pytest.mark.parametrize("mutation,match", [
    ("hash", "Base demo hash"), ("identity", "snapshot_id"),
    ("duplicate", "duplicate sidecar"), ("scenario", "Scenario definition"),
    ("nonfinite", "Nonfinite scenario"), ("length", "vector length"),
    ("missing_contract", "instrument matrix"), ("reconciliation", "reconciliation failed"),
])
def test_mismatches_fail_closed(evidence, mutation, match):
    artifact = deepcopy(evidence[0])
    first = artifact["cases"][0]
    matrix = first["unit_pnl"]["optimization"]
    identifier = next(iter(matrix))
    if mutation == "hash": artifact["meta"]["base_demo_sha256"] = "0" * 64
    elif mutation == "identity": first["snapshot_id"] = "other"
    elif mutation == "duplicate": artifact["cases"][1] = first
    elif mutation == "scenario": artifact["scenario_sets"]["challenge"][0]["elapsed_days"] = 2
    elif mutation == "nonfinite": matrix[identifier][0] = math.nan
    elif mutation == "length": matrix[identifier].pop()
    elif mutation == "missing_contract": del matrix[identifier]
    else:
        held = evidence[1]["snapshots"][0]["portfolio"][0]["contract_id"]
        matrix[held][0] += 1
    with pytest.raises(ValueError, match=match):
        risk_lab.validate_sidecar(artifact, reprice=False)


def test_challenge_matrix_tampering_is_detected_by_full_repricing(evidence):
    artifact = deepcopy(evidence[0])
    matrix = artifact["cases"][0]["unit_pnl"]["challenge"]
    matrix[next(iter(matrix))][0] += 1
    with pytest.raises(ValueError, match="repricing reconciliation"):
        risk_lab.validate_sidecar(artifact)


@pytest.mark.parametrize("scenario,match", [
    (Scenario("negative", "test", parallel_dollars=-100, elapsed_days=1), "shocked future"),
    (Scenario("zero_vol", "test", normal_vol_factor=0, elapsed_days=1), "shocked volatility"),
    (Scenario("expired", "test", elapsed_days=300), "expiry"),
])
def test_invalid_shocks_are_rejected_before_publication(evidence, scenario, match):
    _, demo, source, settings = evidence
    snapshot = source.snapshots[0]
    version = risk_lab._version(demo["cases"][0], snapshot, source.portfolio, settings)
    with pytest.raises(ValueError, match=match):
        risk_lab.unit_pnl(snapshot, version, [scenario])


def test_unknown_omitted_leg_and_negative_cost_scale_are_rejected(evidence):
    artifact, demo, _, _ = evidence
    args = (artifact["cases"][0], demo["cases"][0], demo["snapshots"][0])
    with pytest.raises(ValueError, match="Unknown omitted leg"):
        risk_lab.fixed_ticket(*args, excluded=["NOT_A_TICKET_LEG"])
    with pytest.raises(ValueError, match="Invalid cost scale"):
        risk_lab.fixed_ticket(*args, cost_scale=-1)
