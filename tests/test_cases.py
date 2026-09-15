"""Historical case timing and forecast/transaction identity regressions."""
import json
from pathlib import Path

import pandas as pd
import pytest

from cushing_research.cases import build_cases
from cushing_research.features import inventory_features, public_observations


@pytest.fixture(scope="module")
def historical():
    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "config/research.json").read_text())
    inventory = inventory_features(pd.read_csv(root / "data/processed/inventory.csv"), config)
    quotes = pd.read_csv(root / "data/processed/public_futures.csv")
    return inventory, public_observations(inventory, quotes)


def test_public_cards_preserve_known_then_and_hindsight_separately(historical):
    cases = {c["id"]: c for c in build_cases(*historical)}
    assert set(cases) == {"2020", "2023", "2019"}
    focus = cases["2020"]["anchors"][1]
    assert focus["release_date"] == "2020-04-15"
    assert focus["price_date"] == "2020-04-14"
    assert focus["state"] == "Normal"
    assert focus["inv_delta4"] == pytest.approx(16.520)
    assert "development" in cases["2020"]["model_judgment"]
    assert cases["2020"]["context_sources"][0]["published_date"] > focus["release_date"]
    assert "Retrospective" in cases["2020"]["context_sources"][0]["role"]
    assert len(cases["2023"]["evidence"]) == 21
    assert [r for r in cases["2023"]["evidence"] if r["release_date"] == "2023-11-15"][0]["week_ending"] == "2023-11-10"
    assert "0.079" in cases["2019"]["subsequently_observed"]
    assert "Post-review" in cases["2019"]["selection"]
    for c in cases.values():
        assert c["next_check"] and c["falsifier"] and c["commercial_impact"]
        assert all(r["price_date"] < r["release_date"] and r["sha256"] for r in c["evidence"])


def test_actual_case_uses_test_records_and_keeps_2020_development(historical):
    predictions = []
    for date, pred, actual in [("2023-09-13", .04, -2), ("2023-09-20", .8, .1)]:
        for model in ["B1", "B3"]:
            predictions.append(dict(event_id=date, decision_date=date, model=model, partition="test",
                                    prediction=pred, y=actual, near_contract="TESTA", far_contract="TESTB", inv_z=-1.1))
    legs = [dict(event_id="2023-09-13", contract_id=c, gross_pnl=0, fees=0, slippage=0, net_pnl=0) for c in ["TESTA", "TESTB"]]
    ledger = dict(partition="test", model="B3", cost_ticks=1, force_roundtrip=False, scenario="base",
                  intervals=[dict(event_id="2023-09-13", quantity=0, net_pnl=0)], contract_intervals=legs)
    cases = {c["id"]: c for c in build_cases(*historical, {"predictions": predictions}, [ledger])}
    assert "development" in cases["2020"]["model_judgment"]
    assert "2 decisions" in cases["2023"]["model_judgment"]
    assert len(cases["2023"]["actual_predictions"]) == 4
    failure = cases["failure"]
    assert failure["event_id"] == "2023-09-13"
    assert failure["actual_intervals"][0]["quantity"] == 0
    assert failure["actual_intervals"][0]["net_pnl"] == 0
    assert failure["position"] == "Flat; no position"
    assert "Flat; no position" in failure["summary"]
    assert len(failure["evidence"]) == 9
    assert len(failure["contract_contributions"]) == 2
    assert "largest trade loss" in failure["subsequently_observed"]


def test_equal_maximum_errors_select_first_chronological_event():
    predictions = [dict(event_id=d, decision_date=d, partition="test", model="B3", prediction=.1, y=-.1,
                        near_contract="TESTA", far_contract="TESTB") for d in ["2023-02-08", "2023-02-01"]]
    cases = build_cases(pd.DataFrame(), pd.DataFrame(), {"predictions": predictions}, [])
    assert cases[0]["decision_date"] == "2023-02-01"
