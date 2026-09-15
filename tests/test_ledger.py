"""Hand-checkable invented contracts test accounting only, never market returns."""
import json

import numpy as np
import pandas as pd
import pytest

from cushing_research.ledger import run_ledger


@pytest.fixture
def market():
    dates = pd.date_range("2024-01-04", periods=8, freq="B").strftime("%Y-%m-%d").tolist()
    sessions = pd.DataFrame({"trade_date": dates})
    prices = pd.DataFrame([
        {"trade_date": date, "contract_id": contract, "settlement": value}
        for date in dates for contract, value in (("A", 70.), ("B", 69.), ("C", 68.))
    ])
    config = {"multiplier": 1000, "tick_size": 0.01, "fee_per_contract_side": 2.5,
              "signal_threshold": 0.075, "minimum_economic_intervals": 30}
    return dates, sessions, prices, config


def event(event_id, entry, exit_, near="A", far="B", prediction=.2, **extra):
    return {"event_id": event_id, "entry_date": entry, "exit_date": exit_,
            "near_contract": near, "far_contract": far, "prediction": prediction,
            "model": "B3", "partition": "test", **extra}


def test_same_pair_continuation_is_free_but_forced_roundtrip_is_not(market):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("first", dates[0], dates[2]), event("second", dates[2], dates[4])])
    result = run_ledger(predictions, prices, sessions, config)
    assert result["summary"]["contracts_traded"] == 4
    assert result["summary"]["fees"] == 10
    assert result["summary"]["slippage"] == 40
    assert result["summary"]["net_pnl"] == -50
    assert result["daily"][2]["contracts_traded"] == 0
    assert result["daily"][-1]["positions"] == {}
    continuation = [row for row in result["contract_daily"] if row["trade_date"] == dates[2]]
    assert len(continuation) == 2
    assert all(row["contracts_traded"] == row["fees"] == row["slippage"] == 0 for row in continuation)
    assert all(row["start_event_id"] == "first" and row["end_event_id"] == "second" for row in continuation)
    forced = run_ledger(predictions, prices, sessions, config, force_roundtrip=True)
    assert forced["summary"]["contracts_traded"] == 8
    assert forced["summary"]["net_pnl"] == -100
    forced_boundary = [row for row in forced["contract_daily"] if row["trade_date"] == dates[2]]
    assert all(row["quantity_change"] == 0 and row["contracts_traded"] == 2 for row in forced_boundary)
    assert all(row["fees"] == 5 and row["slippage"] == 20 for row in forced_boundary)
    assert sum(row["net_pnl"] for row in result["intervals"]) == -50


def test_reversal_trades_two_units_per_contract(market):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("long", dates[0], dates[2]), event("short", dates[2], dates[4], prediction=-.2)])
    result = run_ledger(predictions, prices, sessions, config)
    boundary = [trade for trade in result["trades"] if trade["trade_date"] == dates[2]]
    assert {row["contract_id"]: row["quantity_change"] for row in boundary} == {"A": -2, "B": 2}
    assert all(row["closing_quantity"] == row["opening_quantity"] == 1 for row in boundary)
    assert result["summary"]["contracts_traded"] == 8
    assert result["summary"]["net_pnl"] == -100
    attributed = {row["contract_id"]: row for row in result["contract_daily"] if row["trade_date"] == dates[2]}
    assert (attributed["A"]["start_position"], attributed["A"]["quantity_change"], attributed["A"]["end_position"]) == (1, -2, -1)
    assert (attributed["B"]["start_position"], attributed["B"]["quantity_change"], attributed["B"]["end_position"]) == (-1, 2, 1)
    assert all(row["fees"] == 5 and row["slippage"] == 20 for row in attributed.values())


def test_roll_common_contract_nets_two_units_and_old_vm_is_first(market):
    dates, sessions, prices, config = market
    paths = {"A": [70, 71, 73, 73, 73, 73, 73, 73],
             "B": [69, 69, 70, 72, 73, 73, 73, 73],
             "C": [68, 68, 68, 69, 71, 71, 71, 71]}
    for contract, values in paths.items():
        prices.loc[prices.contract_id == contract, "settlement"] = values
    predictions = pd.DataFrame([
        event("A-B", dates[0], dates[2], y=2.),
        event("B-C", dates[2], dates[4], near="B", far="C", y=0.),
    ])
    result = run_ledger(predictions, prices, sessions, config)
    boundary = [trade for trade in result["trades"] if trade["trade_date"] == dates[2]]
    assert {row["contract_id"]: row["quantity_change"] for row in boundary} == {"A": -1, "B": 2, "C": -1}
    assert result["daily"][2]["gross_pnl"] == 1000
    assert result["daily"][2]["positions"] == {"B": 1, "C": -1}
    assert [row["gross_pnl"] for row in result["intervals"]] == [2000, 0]
    assert result["summary"]["gross_pnl"] == 2000
    assert result["summary"]["net_pnl"] == 1900
    assert sum(row["net_pnl"] for row in result["intervals"]) == 1900
    assert result["summary"]["variation_margin_outflow"] == 1000
    assert result["summary"]["top5_positive_gross_day_share"] == 1
    assert result["summary"]["yearly"][0]["net_pnl"] == 1900
    assert result["summary"]["worst_day"]["gross_pnl"] == -1000
    assert result["summary"]["worst_interval"]["event_id"] == "B-C"
    assert result["summary"]["break_even_roundtrip_cost_per_bbl"] == 1
    attributed = {row["contract_id"]: row for row in result["contract_daily"] if row["trade_date"] == dates[2]}
    assert {k: row["quantity_change"] for k, row in attributed.items()} == {"A": -1, "B": 2, "C": -1}
    assert {k: row["gross_pnl"] for k, row in attributed.items()} == {"A": 2000, "B": -1000, "C": 0}
    assert attributed["B"]["start_event_id"] == "A-B"
    assert attributed["B"]["end_event_id"] == "B-C"
    assert attributed["C"]["previous_settlement"] is None
    legs = {(row["event_id"], row["contract_id"]): row for row in result["contract_intervals"]}
    assert legs["A-B", "A"]["gross_pnl"] == 3000
    assert legs["A-B", "B"]["gross_pnl"] == -1000
    assert legs["B-C", "B"]["gross_pnl"] == 3000
    assert legs["B-C", "C"]["gross_pnl"] == -3000
    assert all(row["fees"] == 5 and row["slippage"] == 20 for row in legs.values())
    json.dumps(result, allow_nan=False)


def test_contract_day_hand_calculation_and_terminal_costs(market):
    """One 0.10 near move and one 0.04 far move produce +100 - 40 = 60."""
    dates, sessions, prices, config = market
    prices.loc[(prices.contract_id == "A") & (prices.trade_date == dates[1]), "settlement"] = 70.10
    prices.loc[(prices.contract_id == "B") & (prices.trade_date == dates[1]), "settlement"] = 69.04
    predictions = pd.DataFrame([event("hand", dates[0], dates[1], y=.06)])
    result = run_ledger(predictions, prices, sessions, config, scenario="hand_check")
    rows = {(row["trade_date"], row["contract_id"]): row for row in result["contract_daily"]}
    assert len(rows) == 4
    for contract, quantity, mark in (("A", 1, 70), ("B", -1, 69)):
        entry = rows[dates[0], contract]
        assert entry["start_position"] == 0
        assert entry["previous_settlement"] is None
        assert entry["current_settlement"] == mark
        assert entry["gross_pnl"] == 0
        assert entry["quantity_change"] == entry["end_position"] == quantity
        assert entry["fees"] == 2.5 and entry["slippage"] == 10
        assert entry["net_contribution"] == -12.5
        assert entry["start_event_id"] is None and entry["end_event_id"] == "hand"
        terminal = rows[dates[1], contract]
        assert terminal["start_position"] == quantity
        assert terminal["previous_settlement"] == mark
        assert terminal["quantity_change"] == -quantity and terminal["end_position"] == 0
        assert terminal["fees"] == 2.5 and terminal["slippage"] == 10
        assert terminal["start_event_id"] == "hand" and terminal["end_event_id"] is None
    assert rows[dates[1], "A"]["gross_pnl"] == pytest.approx(100)
    assert rows[dates[1], "B"]["gross_pnl"] == pytest.approx(-40)
    assert result["summary"]["gross_pnl"] == pytest.approx(60)
    assert result["summary"]["net_pnl"] == pytest.approx(10)
    assert sum(row["net_contribution"] for row in rows.values()) == pytest.approx(10)
    assert sum(row["net_pnl"] for row in result["contract_intervals"]) == pytest.approx(10)
    assert result["summary"]["attribution_reconciled"] is True
    assert all(row["scenario"] == "hand_check" for key in ("contract_daily", "contract_intervals", "trades") for row in result[key])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("force_roundtrip", [False, True])
def test_contract_attribution_reconstructs_daily_interval_and_total(market, force_roundtrip):
    """An independent reader can rebuild every layer using the saved fields."""
    dates, sessions, prices, config = market
    for contract, values in {
        "A": [70, 70.1, 70.2, 80, 82, 82, 82, 82],
        "B": [69, 69.04, 69.12, 70, 71, 70, 70, 70],
        "C": [68, 68, 68, 67, 67, 67, 67, 67],
    }.items():
        prices.loc[prices.contract_id == contract, "settlement"] = values
    predictions = pd.DataFrame([
        event("carry1", dates[0], dates[1]),
        event("carry2", dates[1], dates[2]),
        event("roll", dates[2], dates[4], near="B", far="C"),
        event("reverse", dates[4], dates[5], near="B", far="C", prediction=-.2),
        event("flat", dates[5], dates[6], prediction=0),
    ])
    result = run_ledger(predictions, prices, sessions, config, force_roundtrip=force_roundtrip)
    for daily in result["daily"]:
        details = [row for row in result["contract_daily"] if row["trade_date"] == daily["trade_date"]]
        for detail in details:
            calculated = 0 if detail["start_position"] == 0 else detail["start_position"] * 1000 * (detail["current_settlement"] - detail["previous_settlement"])
            assert detail["gross_pnl"] == pytest.approx(calculated)
            assert detail["end_position"] == detail["start_position"] + detail["quantity_change"]
            fills = [row for row in result["trades"] if row["trade_date"] == detail["trade_date"] and row["contract_id"] == detail["contract_id"]]
            assert detail["quantity_change"] == sum(row["quantity_change"] for row in fills)
            assert detail["contracts_traded"] == sum(row["contracts_traded"] for row in fills)
            assert detail["fees"] == sum(row["fees"] for row in fills)
            assert detail["slippage"] == sum(row["slippage"] for row in fills)
        for detail_key, day_key in (("gross_pnl", "gross_pnl"), ("fees", "fees"), ("slippage", "slippage"), ("net_contribution", "net_pnl"), ("contracts_traded", "contracts_traded")):
            assert sum(row[detail_key] for row in details) == pytest.approx(daily[day_key])
    for leg in result["contract_intervals"]:
        marked = [row for row in result["contract_daily"] if row["contract_id"] == leg["contract_id"] and row["start_event_id"] == leg["event_id"]]
        assert leg["gross_pnl"] == pytest.approx(sum(row["gross_pnl"] for row in marked))
        units = sum(
            (fill["closing_quantity"] if fill["outgoing_event_id"] == leg["event_id"] else 0)
            + (fill["opening_quantity"] if fill["incoming_event_id"] == leg["event_id"] else 0)
            for fill in result["trades"] if fill["contract_id"] == leg["contract_id"])
        assert leg["contracts_traded"] == units
        assert leg["fees"] == units * 2.5
        assert leg["slippage"] == units * 10
    for interval in result["intervals"]:
        legs = [row for row in result["contract_intervals"] if row["event_id"] == interval["event_id"]]
        for key in ("gross_pnl", "fees", "slippage", "net_pnl"):
            assert sum(row[key] for row in legs) == pytest.approx(interval[key])
    for key in ("gross_pnl", "fees", "slippage", "net_pnl", "contracts_traded"):
        assert sum(row[key] for row in result["contract_intervals"]) == pytest.approx(result["summary"][key])


def test_negative_settlements_use_dollar_changes_without_logs(market):
    dates, sessions, prices, config = market
    prices.loc[(prices.contract_id == "A") & (prices.trade_date == dates[0]), "settlement"] = -37.63
    prices.loc[(prices.contract_id == "A") & (prices.trade_date == dates[1]), "settlement"] = -10.
    prices.loc[(prices.contract_id == "A") & (prices.trade_date == dates[2]), "settlement"] = 5.
    predictions = pd.DataFrame([event("negative", dates[0], dates[2], y=42.63)])
    result = run_ledger(predictions, prices, sessions, config)
    assert result["summary"]["gross_pnl"] == pytest.approx(42630)
    assert result["summary"]["net_pnl"] == pytest.approx(42580)
    assert next(t for t in result["trades"] if t["contract_id"] == "A")["execution_price"] == pytest.approx(-37.62)


def test_missing_intermediate_held_settlement_fails_but_unused_nan_is_valid(market):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("held", dates[0], dates[4])])
    prices.loc[(prices.contract_id == "C") & (prices.trade_date == dates[1]), "settlement"] = np.nan
    assert run_ledger(predictions, prices, sessions, config)["summary"]["status"] == "complete"
    prices.loc[(prices.contract_id == "A") & (prices.trade_date == dates[1]), "settlement"] = np.nan
    with pytest.raises(ValueError, match="held/traded contract A"):
        run_ledger(predictions, prices, sessions, config)


def test_expiry_and_cap_exit_dates_flatten_before_next_signal(market):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([
        event("expiry", dates[0], dates[1], exit_reason="expiry_buffer"),
        event("cap", dates[4], dates[6], exit_reason="holding_cap"),
    ])
    result = run_ledger(predictions, prices, sessions, config)
    assert all(row["positions"] == {} for row in result["daily"][1:4])
    assert result["daily"][6]["positions"] == {}
    assert [row["holding_sessions"] for row in result["intervals"]] == [1, 2]
    assert [row["exit_reason"] for row in result["intervals"]] == ["expiry_buffer", "holding_cap"]
    assert result["summary"]["flat_session_fraction"] == pytest.approx(4/7)


@pytest.mark.parametrize("forecast,quantity", [(0, 0), (.075, 0), (-.075, 0), (.07501, 1), (-.07501, -1)])
def test_signal_threshold_is_strict_and_zero_has_no_cost(market, forecast, quantity):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("threshold", dates[0], dates[2], prediction=forecast)])
    result = run_ledger(predictions, prices, sessions, config)
    assert result["intervals"][0]["quantity"] == quantity
    assert result["summary"]["net_pnl"] == (-50 if quantity else 0)


def test_cost_stress_does_not_change_signals_and_uses_each_leg_each_side(market):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("cost", dates[0], dates[4])])
    outputs = [run_ledger(predictions, prices, sessions, config, cost_ticks=ticks) for ticks in (1, 2, 4)]
    assert [row["summary"]["net_pnl"] for row in outputs] == [-50, -90, -170]
    assert all(row["summary"]["contracts_traded"] == 4 for row in outputs)
    assert all(row["intervals"][0]["quantity"] == 1 for row in outputs)
    assert all(row["summary"]["economic_evidence"] == "exploratory" for row in outputs)


def test_observed_target_must_reconcile_to_same_contract_pnl(market):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("bad-target", dates[0], dates[2], y=1.)])
    with pytest.raises(ValueError, match="Observed label disagrees"):
        run_ledger(predictions, prices, sessions, config)


def test_empty_model_and_zero_prices_do_not_invent_economic_evidence(market):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("other-model", dates[0], dates[2], model="B1")])
    result = run_ledger(predictions, prices, sessions, config, model="B3")
    assert result["summary"]["status"] == "no_intervals"
    assert result["daily"] == result["trades"] == result["intervals"] == []
    assert result["summary"]["break_even_roundtrip_cost_per_bbl"] is None
    assert result["summary"]["worst_day"] is None
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("problem", ["duplicate_price", "overlap", "missing_entry", "nonfinite_prediction", "two_partitions", "same_leg"])
def test_malformed_ledger_inputs_fail_closed(market, problem):
    dates, sessions, prices, config = market
    predictions = pd.DataFrame([event("first", dates[0], dates[2]), event("second", dates[2], dates[4])])
    if problem == "duplicate_price":
        prices = pd.concat([prices, prices.iloc[:1]], ignore_index=True)
    elif problem == "overlap":
        predictions.loc[1, "entry_date"] = dates[1]
    elif problem == "missing_entry":
        predictions.loc[0, "entry_date"] = "2024-01-06"
    elif problem == "nonfinite_prediction":
        predictions.loc[0, "prediction"] = np.nan
    elif problem == "two_partitions":
        predictions.loc[0, "partition"] = "validation"
    elif problem == "same_leg":
        predictions.loc[0, "near_contract"] = "B"
    with pytest.raises(ValueError):
        run_ledger(predictions, prices, sessions, config)
