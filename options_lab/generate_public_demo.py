#!/usr/bin/env python3
"""Generate and independently verify a public, synthetic fixture lookup asset.

Run with the repository's .venv-options/bin/python. No network input is used.
All nonzero cases start from a fresh calibrated Market and are transacted through
DeskController.set_draft -> preview -> apply. The zero-shift cases retain their
original calibrated Market version. This script does not change model code.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import itertools
import json
import math
from pathlib import Path
import subprocess
import sys
import time

CSO_SHIFTS = [-1, -0.5, 0, 0.5, 1]
VANILLA_SHIFTS_PP = [-5, 0, 5]
TOL = 1e-8


def numeric_difference(left, right, path="", differences=None):
    """Compare every numeric leaf and structure, including solver diagnostics."""
    differences = [] if differences is None else differences
    if isinstance(left, dict) and isinstance(right, dict):
        if set(left) != set(right):
            differences.append({"path": path, "reason": "different keys"})
        for key in left.keys() & right.keys():
            numeric_difference(left[key], right[key], f"{path}.{key}", differences)
    elif isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            differences.append({"path": path, "reason": "different lengths"})
        for i, (a, b) in enumerate(zip(left, right)):
            numeric_difference(a, b, f"{path}[{i}]", differences)
    elif isinstance(left, (int, float)) and not isinstance(left, bool):
        if not isinstance(right, (int, float)) or isinstance(right, bool):
            differences.append({"path": path, "reason": "different numeric type"})
        elif abs(left - right) > TOL:
            differences.append({"path": path, "absolute_difference": abs(left - right)})
    elif left != right:
        differences.append({"path": path, "reason": "different nonnumeric value"})
    return differences


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "options_lab_runs/public_demo_data")
    args = parser.parse_args()
    root, output = args.repo_root.resolve(), args.output_dir.resolve()
    sys.path.insert(0, str(root))
    from options_lab.adapters import load_json
    from options_lab.controller import DeskController
    from options_lab.engine import evaluate
    from options_lab.models import ENGINE_VERSION, VolNode, VolVersion, portfolio_id, stable_id
    from options_lab.pricing import instrument_value
    from options_lab.scenarios import build_scenarios, scenario_vol, shocked_snapshot
    from options_lab.volatility import calibrate_market, validate_version, vol_for_contract

    started = time.monotonic()
    fixture_path = root / "options_lab/examples/engineering_replay.json"
    source = load_json(fixture_path)
    assert len(source.snapshots) == 3
    assert all(s.mode == "engineering_fixture" for s in source.snapshots)
    assert source.settings is not None
    settings = replace(source.settings, allow_cso_hedge=False)
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    tracked_status = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True).strip()
    generated_at = datetime.now(timezone.utc).isoformat()
    artifact = {
        "meta": {
            "title": "WTI Options Desk — precomputed engineering fixtures",
            "engine_version": ENGINE_VERSION,
            "source_commit": commit,
            "generated_at": generated_at,
            "data_label": "SYNTHETIC ENGINEERING FIXTURE — invented prices and expiry metadata; not observed market data",
            "limitations": [
                "Synthetic engineering fixtures demonstrate model and software mechanics; no real-market calibration, execution, or historical hedge-performance evidence.",
                "Exactly 45 precomputed engine evaluations. UI controls select saved cases; no interpolation or live pricing.",
                "Each case starts from that snapshot's calibrated Market. CSO shifts are additive USD/bbl/sqrt(year); vanilla shifts are additive percentage points across every vanilla slice.",
                "European CSOs use Bachelier normal volatility; European vanilla options use Black-76 lognormal volatility. These volatilities have different units.",
                "Direct CSO hedges are disabled. All other fixture portfolio and settings inputs are retained, including one-day horizon, 100 synthetic fully repriced scenarios, fees and lot limits.",
                "Quotes are indicative inventory-aware model quotes; displayed size is a constraint result, not a fill prediction or an executable order.",
                "Worst stress losses use synthetic scenarios, not historical VaR. Costs are fixture BBO crossing costs plus the configured fee.",
                "Hedge solver statuses, messages, gaps and termination reasons are preserved verbatim. Bounded incumbents and fallback results do not establish global optimality.",
                "Time-guard incumbents can vary across runs. Consult verification.json for time-guard flags and three representative repeat evaluations.",
            ],
            "cso_shifts": CSO_SHIFTS,
            "vanilla_shifts_pp": VANILLA_SHIFTS_PP,
            "case_count": 45,
        },
        "snapshots": [],
        "cases": [],
    }
    versions = {}
    generation_time_guards = []
    preview_differences = []

    def record_guard(bundle, key, phase):
        if bundle["reproducibility"]["time_guard_triggered"]:
            generation_time_guards.append({"case": list(key), "phase": phase})

    for index, snapshot in enumerate(source.snapshots):
        market = calibrate_market(snapshot)
        artifact["snapshots"].append({
            "index": index, "as_of": snapshot.as_of, "snapshot_id": snapshot.snapshot_id,
            "source": snapshot.source, "mode": snapshot.mode,
            "contracts": [asdict(c) for c in snapshot.contracts],
            "market_nodes": [asdict(n) for n in market.nodes],
            "market_vol_version_id": market.version_id,
            "portfolio": [asdict(p) for p in source.portfolio], "settings": asdict(settings),
        })
        for cso_shift, vanilla_shift in itertools.product(CSO_SHIFTS, VANILLA_SHIFTS_PP):
            key = (index, cso_shift, vanilla_shift)
            desk = DeskController(snapshot, source.portfolio, settings)
            record_guard(desk.bundle, key, "initial_market")
            if cso_shift or vanilla_shift:
                nodes = tuple(replace(n, value=n.value + (cso_shift if n.model == "normal" else vanilla_shift / 100.0))
                              for n in desk.market_vol.nodes)
                desk.set_draft(nodes)
                preview = desk.preview()
                assert preview["basis_market"] == snapshot.snapshot_id
                assert preview["basis_portfolio"] == portfolio_id(source.portfolio)
                record_guard(preview["before"], key, "preview_before")
                record_guard(preview["after"], key, "preview_after")
                bundle = desk.apply(expected_generation=desk.generation)
                record_guard(bundle, key, "apply")
                differences = numeric_difference(preview["after"], bundle)
                if differences:
                    preview_differences.append({"case": list(key), "differences": differences})
            else:
                bundle = desk.bundle
            versions[key] = desk.active_vol
            artifact["cases"].append({
                "snapshot_index": index, "cso_shift": cso_shift, "vanilla_shift_pp": vanilla_shift,
                "active_nodes": [asdict(n) for n in desk.active_vol.nodes],
                "active_vol_metadata": {"label": desk.active_vol.label, "origin": desk.active_vol.origin,
                                        "parent_id": desk.active_vol.parent_id, "created_at": desk.active_vol.created_at,
                                        "version_id": desk.active_vol.version_id},
                "bundle": bundle,
            })
            print(f"Generated {len(artifact['cases'])}/45: snapshot={index}, CSO={cso_shift}, vanilla_pp={vanilla_shift}", flush=True)

    output.mkdir(parents=True, exist_ok=True)
    data_path = output / "demo-data.json"
    data_path.write_text(json.dumps(artifact, separators=(",", ":"), ensure_ascii=False, allow_nan=False) + "\n")
    # Verify the serialized asset, against the separately reloaded original fixture.
    data = json.loads(data_path.read_text())
    original = load_json(fixture_path)
    failures = []
    max_errors = {"engine_reconciliation": 0.0, "independent_repricing": 0.0,
                  "contribution_sum": 0.0, "gross_minus_cost_net": 0.0, "trade_cost": 0.0,
                  "summary": 0.0, "price": 0.0}
    check_count = 0

    def check(condition, label, key=None):
        nonlocal check_count
        check_count += 1
        if not condition:
            failures.append({"case": None if key is None else list(key), "check": label})

    def close(a, b, label, category, key):
        error = abs(float(a) - float(b))
        max_errors[category] = max(max_errors[category], error)
        check(error <= TOL, label, key)

    def verify_summary(saved, values, key, label):
        expected = {"worst_abs_pnl": max(map(abs, values)), "worst_loss": max(0.0, -min(values)),
                    "scenario_rms": math.sqrt(sum(v*v for v in values)/len(values)),
                    "scenario_mean": sum(values)/len(values), "scenario_count": len(values)}
        for name, value in expected.items():
            close(saved[name], value, f"{label} summary {name}", "summary", key)

    check(len(data["cases"]) == 45 and data["meta"]["case_count"] == 45, "exactly 45 cases")
    keys = [(c["snapshot_index"], c["cso_shift"], c["vanilla_shift_pp"]) for c in data["cases"]]
    check(set(keys) == set(itertools.product(range(3), CSO_SHIFTS, VANILLA_SHIFTS_PP)) and len(set(keys)) == 45,
          "complete unique exact case grid")
    check(not tracked_status, "tracked source tree is clean at generation")
    for saved, snapshot in zip(data["snapshots"], original.snapshots):
        expected_market = calibrate_market(snapshot)
        check(saved["snapshot_id"] == snapshot.snapshot_id, "original snapshot identity")
        check(saved["contracts"] == json.loads(json.dumps([asdict(c) for c in snapshot.contracts])), "unaltered raw contracts")
        check(saved["market_nodes"] == json.loads(json.dumps([asdict(n) for n in expected_market.nodes])), "original calibrated nodes")
        check(saved["portfolio"] == [asdict(p) for p in original.portfolio], "unaltered original portfolio")
        check(saved["settings"] == asdict(replace(original.settings, allow_cso_hedge=False)), "only authorized setting override")
        check(saved["market_vol_version_id"] == expected_market.version_id, "market volatility ID")

    for case in data["cases"]:
        key = (case["snapshot_index"], case["cso_shift"], case["vanilla_shift_pp"])
        snapshot = original.snapshots[key[0]]
        bundle = case["bundle"]
        market = calibrate_market(snapshot)
        if key[1] or key[2]:
            expected_nodes = tuple(replace(n, value=n.value+(key[1] if n.model == "normal" else key[2]/100),
                                           origin="manual", as_of=snapshot.as_of,
                                           source="User adjustment; base: "+n.source) for n in market.nodes)
            expected_version = VolVersion(expected_nodes, "Manual draft", "manual", market.version_id, snapshot.as_of)
        else:
            expected_version = market
        actual_nodes = tuple(VolNode.from_dict(n) for n in case["active_nodes"])
        check(actual_nodes == expected_version.nodes, "all active nodes equal exact additive shift", key)
        check(not validate_version(expected_version, snapshot), "active node validation", key)
        check(bundle["status"] == "analysis_only", "expected analysis_only status", key)
        expected_ids = {"snapshot_id": snapshot.snapshot_id, "vol_version_id": expected_version.version_id,
                        "portfolio_id": portfolio_id(original.portfolio), "settings_id": settings.settings_id,
                        "engine_version": ENGINE_VERSION}
        check(all(bundle[k] == v for k, v in expected_ids.items()), "bundle input identities", key)
        check(bundle["bundle_id"] == stable_id(expected_ids, "bundle-"), "bundle content-addressed identity", key)
        check(bundle["as_of"] == snapshot.as_of and bundle["mode"] == snapshot.mode, "bundle snapshot metadata", key)
        check(case["active_vol_metadata"]["version_id"] == expected_version.version_id, "active metadata identity", key)
        max_errors["engine_reconciliation"] = max(max_errors["engine_reconciliation"], abs(bundle["risk"]["reconciliation_error"]))
        check(bundle["risk"]["reconciliation_error"] <= TOL, "engine reconciliation", key)
        check(set(p["contract_id"] for p in bundle["prices"]) == set(snapshot.contract_map), "full original contract price coverage", key)
        for quote in bundle["quotes"]:
            check(quote["bid_size"] >= 0 and quote["ask_size"] >= 0, "nonnegative quote sizes", key)
            for side in ("bid", "ask"):
                if quote[f"{side}_size"] > 0:
                    check(quote[side] is not None and quote[side] >= 0, f"nonnegative positive-size {side}", key)
            if quote["bid"] is not None and quote["ask"] is not None:
                check(0 <= quote["bid"] < quote["ask"], "nonnegative uncrossed indicative quote", key)
        scenarios = build_scenarios(settings)
        markets = [shocked_snapshot(snapshot, s) for s in scenarios]
        pnl = {}
        for contract in snapshot.contracts:
            vol = 0 if contract.kind == "future" else vol_for_contract(contract, expected_version)
            base_price = instrument_value(contract, snapshot, vol)
            saved_price = next(p for p in bundle["prices"] if p["contract_id"] == contract.contract_id)
            close(saved_price["model_price"], base_price, "independent base price", "price", key)
            pnl[contract.contract_id] = [(instrument_value(contract, sm, scenario_vol(contract.kind, vol, scenario))-base_price)*contract.multiplier
                                         for scenario, sm in zip(scenarios, markets)]
        check(len(bundle["risk"]["scenarios"]) == settings.scenario_count, "full scenario coverage", key)
        for method, hedge in bundle["hedges"].items():
            total_cost = 0.0
            total_lots = 0
            for trade in hedge["trades"]:
                quantity = trade["quantity"]
                check(type(quantity) is int and quantity != 0, "nonzero integer hedge lots", key)
                contract = snapshot.contract_map[trade["contract_id"]]
                check(contract.kind != "cso", "direct CSO hedges disabled", key)
                check(method != "delta" or contract.kind == "future", "delta hedge uses futures only", key)
                quote = snapshot.quote_map[contract.contract_id]
                per_lot = (quote.ask-quote.bid)/2*contract.multiplier+settings.fee_per_contract
                close(trade["cost_per_lot"], per_lot, "raw BBO and fee cost per lot", "trade_cost", key)
                close(trade["total_cost"], abs(quantity)*per_lot, "raw BBO and fee trade cost", "trade_cost", key)
                check(trade["lots"] == abs(quantity) and trade["direction"] == ("buy" if quantity > 0 else "sell"), "trade direction and lots", key)
                check(abs(quantity) <= (settings.future_bound if contract.kind == "future" else settings.option_bound), "per-instrument trade bound", key)
                total_cost += abs(quantity)*per_lot
                total_lots += abs(quantity)
            close(hedge["cost"], total_cost, "total recomputed hedge cost", "trade_cost", key)
            check(total_lots <= settings.gross_lot_limit, "gross lot limit", key)
            check(hedge["position_constraints"]["compliant"], "complete portfolio position compliance", key)
            verify_summary(bundle["risk"]["summary"][method], hedge["net_pnl"], key, method)
        for i, row in enumerate(bundle["risk"]["scenarios"]):
            unhedged = sum(p.quantity*pnl[p.contract_id][i] for p in original.portfolio)
            close(row["unhedged"], unhedged, "independent portfolio repricing", "independent_repricing", key)
            close(row["unhedged"], sum(row["contributions"].values()), "contributions sum", "contribution_sum", key)
            for position in original.portfolio:
                close(row["contributions"][position.contract_id], position.quantity*pnl[position.contract_id][i], "position contribution", "independent_repricing", key)
            for method, hedge in bundle["hedges"].items():
                gross = unhedged+sum(t["quantity"]*pnl[t["contract_id"]][i] for t in hedge["trades"])
                close(row[f"{method}_gross"], gross, "independent gross hedge repricing", "independent_repricing", key)
                close(row[f"{method}_net"], gross-hedge["cost"], "gross minus cost equals net", "gross_minus_cost_net", key)
                close(hedge["gross_pnl"][i], gross, "hedge vector gross", "independent_repricing", key)
                close(hedge["net_pnl"][i], gross-hedge["cost"], "hedge vector net", "gross_minus_cost_net", key)
        verify_summary(bundle["risk"]["summary"]["unhedged"], [r["unhedged"] for r in bundle["risk"]["scenarios"]], key, "unhedged")

    repeat_results = []
    for key in [(0, 0, 0), (1, -1, -5), (2, 1, 5)]:
        case = next(c for c in data["cases"] if (c["snapshot_index"], c["cso_shift"], c["vanilla_shift_pp"]) == key)
        repeated = evaluate(original.snapshots[key[0]], versions[key], original.portfolio, settings)
        record_guard(repeated, key, "repeat_evaluate")
        diffs = numeric_difference(case["bundle"], repeated)
        guard = case["bundle"]["reproducibility"]["time_guard_triggered"] or repeated["reproducibility"]["time_guard_triggered"]
        repeat_results.append({"case": list(key), "numeric_and_structural_match": not diffs,
                               "time_guard_triggered": guard, "differences": diffs})
        check(not diffs, "representative repeat numeric and structural match", key)
    time_guard_cases = [list(k) for k, case in zip(keys, data["cases"]) if case["bundle"]["reproducibility"]["time_guard_triggered"]]
    baseline = next(c for c in data["cases"] if (c["snapshot_index"], c["cso_shift"], c["vanilla_shift_pp"]) == (0, 0, 0))
    verification = {
        "generated_at": generated_at, "source_commit": commit, "engine_version": ENGINE_VERSION,
        "data_file": data_path.name, "data_sha256": hashlib.sha256(data_path.read_bytes()).hexdigest(),
        "fixture_file": str(fixture_path.relative_to(root)), "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
        "source_tracked_changes": tracked_status, "case_count": len(data["cases"]), "snapshot_count": len(data["snapshots"]),
        "check_count": check_count, "failure_count": len(failures), "failures": failures,
        "numeric_tolerance": TOL, "max_reconciliation_errors": max_errors,
        "status_counts": dict(Counter(c["bundle"]["status"] for c in data["cases"])),
        "hedge_status_counts": {m: dict(Counter(c["bundle"]["hedges"][m]["status"] for c in data["cases"])) for m in ("delta", "proxy")},
        "time_guard_nondeterminism_flag": bool(generation_time_guards),
        "saved_case_time_guards": time_guard_cases, "all_observed_time_guards": generation_time_guards,
        "preview_apply_differences": preview_differences, "representative_repeat_evaluations": repeat_results,
        "baseline_reference": {"snapshot_index": 0, "cso_shift": 0, "vanilla_shift_pp": 0,
                               "bundle_id": baseline["bundle"]["bundle_id"], "quote": baseline["bundle"]["quotes"][0],
                               "risk_summary": baseline["bundle"]["risk"]["summary"],
                               "hedges": {m: {k: baseline["bundle"]["hedges"][m].get(k) for k in ("status", "termination_reason", "cost", "trades", "mip_gap")} for m in ("delta", "proxy")}},
        "elapsed_seconds": time.monotonic()-started,
        "passed": not failures,
        "scope": "Fixture asset validation and three repeated engine evaluations only. No real-market, live, or historical performance acceptance claim.",
    }
    (output / "verification.json").write_text(json.dumps(verification, indent=2, ensure_ascii=False, allow_nan=False)+"\n")
    print(json.dumps({"data_bytes": data_path.stat().st_size, "verification_bytes": (output/"verification.json").stat().st_size,
                      "checks": check_count, "failures": len(failures), "time_guard_flag": bool(generation_time_guards),
                      "max_errors": max_errors, "elapsed_seconds": verification["elapsed_seconds"]}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
