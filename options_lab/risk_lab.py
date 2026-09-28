"""Export fixed-ticket stress experiments; never optimize or mutate saved cases.

Run ``python -m options_lab.risk_lab`` to create the separate public sidecar.
Run ``python -m options_lab.risk_lab --check`` to verify the checked-in sidecar.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import itertools
import json
import math
from pathlib import Path
import subprocess

from .adapters import load_json
from .models import ENGINE_VERSION, VolNode, VolVersion, portfolio_id, stable_id
from .pricing import instrument_value, time_to_expiry
from .scenarios import Scenario, build_scenarios, scenario_vol, shocked_snapshot
from .volatility import calibrate_market, validate_version, vol_for_contract

ROOT = Path(__file__).resolve().parents[1]
DEMO = "docs/options-desk/demo-data.json"
OUTPUT = "docs/options-desk/risk-lab-data.json"
FIXTURE = "options_lab/examples/engineering_replay.json"
TOLERANCE_USD = 0.01
SOURCE_FILES = tuple("options_lab/" + name + ".py" for name in
                     ("risk_lab", "adapters", "models", "pricing", "scenarios", "volatility"))


def challenge_scenarios():
    """Fixed before evaluation; no historical probabilities or retuning."""
    specifications = (
        ("parallel_up_12", "parallel", 12, 0, 1, 1, 0),
        ("parallel_down_12", "parallel", -12, 0, 1, 1, 0),
        ("twist_up_5", "curve", 0, 5, 1, 1, 0),
        ("twist_down_5", "curve", 0, -5, 1, 1, 0),
        ("normal_up_vanilla_down", "basis", 0, 0, 1.8, .7, 0),
        ("normal_down_vanilla_up", "basis", 0, 0, .6, 1.5, 0),
        ("normal_add_2", "basis", 0, 0, 1, 1, 2),
        ("vanilla_only_up", "basis", 0, 0, 1, 1.6, 0),
        ("down_steep", "joint", -12, 5, 1.8, .7, 0),
        ("down_flat", "joint", -12, -5, 1.8, .7, 0),
        ("up_steep", "joint", 12, 5, .6, 1.5, 0),
        ("up_flat", "joint", 12, -5, .6, 1.5, 0),
    )
    return tuple(Scenario(name=name, category=category, parallel_dollars=parallel,
                          twist_dollars=twist, normal_vol_factor=normal,
                          lognormal_vol_factor=lognormal, normal_vol_additive=additive,
                          elapsed_days=1.0)
                 for name, category, parallel, twist, normal, lognormal, additive in specifications)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _signature(scenario):
    return tuple(getattr(scenario, name) for name in
                 ("parallel_dollars", "twist_dollars", "normal_vol_factor",
                  "lognormal_vol_factor", "normal_vol_additive", "elapsed_days"))


def _inputs(root):
    demo = json.loads((root / DEMO).read_text())
    source = load_json(root / FIXTURE)
    settings = replace(source.settings, allow_cso_hedge=False)
    require(len(source.snapshots) == len(demo["snapshots"]) == 3, "Expected three fixture snapshots")
    require(settings.horizon_days == 1 and settings.scenario_count == 100, "Unexpected scenario settings")
    require(len(demo["cases"]) == 45, "Expected 45 original cases")
    expected = set(itertools.product(range(3), (-1, -.5, 0, .5, 1), (-5, 0, 5)))
    keys = [(c["snapshot_index"], c["cso_shift"], c["vanilla_shift_pp"]) for c in demo["cases"]]
    require(len(set(keys)) == 45 and set(keys) == expected, "Incomplete original case grid")
    for index, snapshot in enumerate(source.snapshots):
        saved = demo["snapshots"][index]
        require(snapshot.mode == "engineering_fixture", "Only synthetic fixtures are permitted")
        require(saved["snapshot_id"] == snapshot.snapshot_id, "Original snapshot identity mismatch")
        require(saved["settings"] == asdict(settings), "Original settings mismatch")
        require(saved["portfolio"] == [asdict(p) for p in source.portfolio], "Original portfolio mismatch")
        require(saved["contracts"] == json.loads(json.dumps([asdict(c) for c in snapshot.contracts])),
                "Original contract metadata mismatch")
    return demo, source, settings


def _version(case, snapshot, portfolio, settings, saved_snapshot):
    metadata = case["active_vol_metadata"]
    version = VolVersion(tuple(VolNode.from_dict(n) for n in case["active_nodes"]),
                         metadata["label"], metadata["origin"], metadata["parent_id"], metadata["created_at"])
    # Recorded nodes determine identity. Fresh IV inversion can differ by a few
    # floating-point ULPs across supported platforms and is a numerical check,
    # not a replacement for the immutable, hashed market version.
    market = VolVersion(tuple(VolNode.from_dict(n) for n in saved_snapshot["market_nodes"]),
                        "Market calibration", "calibrated", None, snapshot.as_of)
    require(market.version_id == saved_snapshot["market_vol_version_id"],
            "Saved market volatility identity mismatch")
    recalibrated = calibrate_market(snapshot)
    require(len(market.nodes) == len(recalibrated.nodes), "Market calibration coverage mismatch")
    for recorded, fresh in zip(market.nodes, recalibrated.nodes):
        require(replace(fresh, value=recorded.value) == recorded,
                "Market calibration metadata mismatch")
        require(math.isclose(recorded.value, fresh.value, rel_tol=0.0, abs_tol=1e-10),
                "Market calibration value mismatch")
    if case["cso_shift"] or case["vanilla_shift_pp"]:
        nodes = tuple(replace(n, value=n.value + (case["cso_shift"] if n.model == "normal"
                                                 else case["vanilla_shift_pp"] / 100),
                              origin="manual", as_of=snapshot.as_of,
                              source="User adjustment; base: " + n.source) for n in market.nodes)
        expected = VolVersion(nodes, "Manual draft", "manual", market.version_id, snapshot.as_of)
    else:
        expected = market
    require(version == expected and metadata["version_id"] == version.version_id, "Active volatility identity mismatch")
    require(not validate_version(version, snapshot), "Invalid active volatility")
    identity = dict(snapshot_id=snapshot.snapshot_id, vol_version_id=version.version_id,
                    portfolio_id=portfolio_id(portfolio), settings_id=settings.settings_id,
                    engine_version=ENGINE_VERSION)
    bundle = case["bundle"]
    require(all(bundle[k] == v for k, v in identity.items()), "Original bundle identity mismatch")
    require(bundle["bundle_id"] == stable_id(identity, "bundle-"), "Original bundle ID mismatch")
    require(bundle["status"] == "analysis_only", "Unavailable original case")
    return version


def unit_pnl(snapshot, version, scenarios):
    """Full one-contract dollar repricing, including decay and no trade costs."""
    shocked = [shocked_snapshot(snapshot, scenario) for scenario in scenarios]
    for market in shocked:
        require(all(q.mid is not None and math.isfinite(q.mid) and q.mid > 0
                    for q in market.quotes if market.contract_map[q.contract_id].kind == "future"),
                "Nonpositive or nonfinite shocked future")
    result = {}
    for contract in snapshot.contracts:
        vol = 0.0 if contract.kind == "future" else vol_for_contract(contract, version)
        original = instrument_value(contract, snapshot, vol)
        values = []
        for scenario, market in zip(scenarios, shocked):
            shocked_vol = scenario_vol(contract.kind, vol, scenario)
            require(contract.kind == "future" or (math.isfinite(shocked_vol) and shocked_vol > 0),
                    "Nonpositive or nonfinite shocked volatility")
            require(time_to_expiry(contract, market) > 0, "Challenge crosses contract expiry")
            value = (instrument_value(contract, market, shocked_vol) - original) * contract.multiplier
            require(math.isfinite(value), "Nonfinite unit P&L")
            values.append(value)
        result[contract.contract_id] = values
    return result


def fixed_ticket(case, original, snapshot, scenario_set="optimization", method="proxy", excluded=(), cost_scale=1.0):
    """Counterfactual omission at inception; not closing an already filled leg."""
    require(method in ("delta", "proxy"), "Unknown hedge method")
    require(math.isfinite(cost_scale) and cost_scale >= 0, "Invalid cost scale")
    matrix = case["unit_pnl"][scenario_set]
    count = len(next(iter(matrix.values())))
    base = [sum(p["quantity"] * matrix[p["contract_id"]][i] for p in snapshot["portfolio"])
            for i in range(count)]
    trades = original["bundle"]["hedges"][method]["trades"]
    excluded = set(excluded)
    require(excluded <= {t["contract_id"] for t in trades}, "Unknown omitted leg")
    retained = [t for t in trades if t["contract_id"] not in excluded]
    gross = [base[i] + sum(t["quantity"] * matrix[t["contract_id"]][i] for t in retained)
             for i in range(count)]
    cost = sum(t["total_cost"] for t in retained) * cost_scale
    net = [value - cost for value in gross]
    positions = {p["contract_id"]: p["quantity"] for p in snapshot["portfolio"]}
    for trade in retained:
        positions[trade["contract_id"]] = positions.get(trade["contract_id"], 0) + trade["quantity"]
    settings = snapshot["settings"]
    objective = max(map(abs, gross)) + cost
    return dict(base_pnl=base, gross_pnl=gross, net_pnl=net, cost=cost,
                worst_loss=max(0.0, -min(net)), objective=objective,
                risk_compliant=objective <= settings["risk_limit_dollars"] + TOLERANCE_USD,
                position_compliant=all(abs(q) <= settings["position_limit"] for q in positions.values()),
                gross_lots=sum(abs(t["quantity"]) for t in retained), final_positions=positions)


def validate_sidecar(artifact, root=ROOT, *, reprice=True):
    root = Path(root)
    demo, source, settings = _inputs(root)
    require(artifact["schema_version"] == "1.0", "Unsupported sidecar schema")
    meta = artifact["meta"]
    require(meta["base_demo_sha256"] == sha256(root / DEMO), "Base demo hash mismatch")
    require(meta["fixture_sha256"] == sha256(root / FIXTURE), "Fixture hash mismatch")
    require(meta["base_demo_source_commit"] == demo["meta"]["source_commit"], "Base source identity mismatch")
    require(meta["engine_version"] == ENGINE_VERSION and meta["optimizer_rerun"] is False,
            "Invalid experiment identity")
    require(meta["source_sha256"] == {name: sha256(root / name) for name in SOURCE_FILES}, "Source hash mismatch")
    groups = {"optimization": build_scenarios(settings), "challenge": challenge_scenarios()}
    require(artifact["scenario_sets"] == {k: [asdict(s) for s in v] for k, v in groups.items()},
            "Scenario definition mismatch")
    original_signatures = {_signature(s) for s in groups["optimization"]}
    challenge_signatures = {_signature(s) for s in groups["challenge"]}
    require(len(challenge_signatures) == 12 and not original_signatures & challenge_signatures,
            "Challenges overlap or repeat")
    by_id = {case["bundle_id"]: case for case in artifact["cases"]}
    require(len(by_id) == len(artifact["cases"]) == 45, "Incomplete or duplicate sidecar cases")
    errors = {"repricing": 0.0, "base": 0.0, "gross": 0.0, "net": 0.0, "cost": 0.0,
              "all_legs_removed": 0.0, "single_leg_removed": 0.0}
    checks = 0
    challenge_breaches = {"delta": 0, "proxy": 0}

    def close(left, right, category):
        nonlocal checks
        require(math.isfinite(left) and math.isfinite(right), "Nonfinite comparison")
        error = abs(left - right)
        errors[category] = max(errors[category], error)
        checks += 1
        require(error <= TOLERANCE_USD, f"{category} reconciliation failed: {error}")

    for original in demo["cases"]:
        bundle = original["bundle"]
        case = by_id.get(bundle["bundle_id"])
        require(case is not None, "Missing original case")
        index = original["snapshot_index"]
        snapshot, saved_snapshot = source.snapshots[index], demo["snapshots"][index]
        version = _version(original, snapshot, source.portfolio, settings, saved_snapshot)
        for key in ("bundle_id", "snapshot_id", "vol_version_id", "portfolio_id", "settings_id"):
            require(case[key] == bundle[key], f"Sidecar {key} mismatch")
        for key in ("snapshot_index", "cso_shift", "vanilla_shift_pp"):
            require(case[key] == original[key], f"Sidecar {key} mismatch")
        require(set(case["unit_pnl"]) == set(groups), "Unexpected scenario sets")
        for group, scenarios in groups.items():
            matrix = case["unit_pnl"][group]
            require(set(matrix) == set(snapshot.contract_map), "Incomplete instrument matrix")
            expected = unit_pnl(snapshot, version, scenarios) if reprice else None
            for identifier, values in matrix.items():
                require(len(values) == len(scenarios), "Incorrect scenario vector length")
                require(all(type(v) in (int, float) and math.isfinite(v) for v in values), "Nonfinite scenario vector")
                if expected is not None:
                    for value, reference in zip(values, expected[identifier]):
                        close(value, reference, "repricing")
            for method in ("delta", "proxy"):
                hedge = bundle["hedges"][method]
                require(hedge["status"] in ("optimal", "feasible_limit", "fallback_zero", "fallback_futures"),
                        "Unavailable saved hedge")
                require(hedge["position_constraints"]["compliant"], "Noncompliant saved positions")
                ticket = fixed_ticket(case, original, saved_snapshot, group, method)
                for trade in hedge["trades"]:
                    require(type(trade["quantity"]) is int, "Noninteger original trade")
                    require(trade["cost_per_lot"] >= 0 and trade["total_cost"] >= 0, "Negative trade cost")
                    contract = snapshot.contract_map[trade["contract_id"]]
                    quote = snapshot.quote_map[trade["contract_id"]]
                    half_spread = ((quote.ask - quote.bid) / 2 if quote.kind == "bbo"
                                   and quote.ask is not None and quote.bid is not None else contract.tick_size)
                    close(trade["cost_per_lot"], half_spread * contract.multiplier + settings.fee_per_contract, "cost")
                    close(trade["total_cost"], abs(trade["quantity"]) * trade["cost_per_lot"], "cost")
                close(ticket["cost"], hedge["cost"], "cost")
                if group == "optimization":
                    for i, row in enumerate(bundle["risk"]["scenarios"]):
                        require(row["name"] == scenarios[i].name, "Original scenario order mismatch")
                        close(ticket["base_pnl"][i], row["unhedged"], "base")
                        close(ticket["gross_pnl"][i], hedge["gross_pnl"][i], "gross")
                        close(ticket["net_pnl"][i], hedge["net_pnl"][i], "net")
                    close(ticket["worst_loss"], bundle["risk"]["summary"][method]["worst_loss"], "net")
                    close(ticket["objective"], hedge["objective"], "gross")
                else:
                    challenge_breaches[method] += int(not ticket["risk_compliant"])
                omitted = [t["contract_id"] for t in hedge["trades"]]
                empty = fixed_ticket(case, original, saved_snapshot, group, method, omitted)
                close(empty["cost"], 0, "cost")
                for value, reference in zip(empty["net_pnl"], ticket["base_pnl"]):
                    close(value, reference, "all_legs_removed")
                for trade in hedge["trades"]:
                    removed = fixed_ticket(case, original, saved_snapshot, group, method, [trade["contract_id"]])
                    for i, value in enumerate(removed["net_pnl"]):
                        reference = ticket["net_pnl"][i] - trade["quantity"] * matrix[trade["contract_id"]][i] + trade["total_cost"]
                        close(value, reference, "single_leg_removed")
                previous = -math.inf
                for scale in (1, 2, 4):
                    stressed = fixed_ticket(case, original, saved_snapshot, group, method, cost_scale=scale)
                    require(stressed["worst_loss"] + 1e-9 >= previous, "Cost sensitivity is not monotone")
                    previous = stressed["worst_loss"]
    return dict(passed=True, case_count=45, optimization_scenarios=100, challenge_scenarios=12,
                tolerance_usd=TOLERANCE_USD, reconciliation_checks=checks,
                maximum_errors_usd=errors, repriced=reprice,
                challenge_risk_limit_breaches=challenge_breaches,
                original_ticket_quantities_unchanged=True, optimizer_rerun=False)


def build_sidecar(root=ROOT):
    root = Path(root)
    demo, source, settings = _inputs(root)
    groups = {"optimization": build_scenarios(settings), "challenge": challenge_scenarios()}
    artifact = dict(schema_version="1.0", meta={
        "base_demo_sha256": sha256(root / DEMO), "fixture_sha256": sha256(root / FIXTURE),
        "base_demo_source_commit": demo["meta"]["source_commit"],
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "source_commit_note": "Repository base commit; exact exporter/pricer contents are identified by source_sha256.",
        "source_sha256": {name: sha256(root / name) for name in SOURCE_FILES},
        "engine_version": ENGINE_VERSION, "data_label": "Synthetic fixed-ticket stress experiments",
        "pnl_unit": "USD per one contract, full repricing before trade costs",
        "cost_policy": "Read original trade total_cost; omission is at inception, not a free unwind.",
        "challenge_policy": "Twelve fixed additional synthetic stresses; not used to optimize saved tickets; no market OOS, probability, or VaR claim.",
        "optimizer_rerun": False,
    }, scenario_sets={k: [asdict(s) for s in v] for k, v in groups.items()}, cases=[])
    for original in demo["cases"]:
        snapshot = source.snapshots[original["snapshot_index"]]
        version = _version(original, snapshot, source.portfolio, settings,
                           demo["snapshots"][original["snapshot_index"]])
        case = {key: original["bundle"][key] for key in
                ("bundle_id", "snapshot_id", "vol_version_id", "portfolio_id", "settings_id")}
        case.update({key: original[key] for key in ("snapshot_index", "cso_shift", "vanilla_shift_pp")})
        case["unit_pnl"] = {key: unit_pnl(snapshot, version, scenarios) for key, scenarios in groups.items()}
        artifact["cases"].append(case)
    artifact["validation"] = validate_sidecar(artifact, root)
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        artifact = json.loads(args.output.read_text())
        report = validate_sidecar(artifact)
    else:
        artifact = build_sidecar()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(artifact, separators=(",", ":"), allow_nan=False) + "\n")
        report = validate_sidecar(json.loads(args.output.read_text()))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
