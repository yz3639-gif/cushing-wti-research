"""Settlement-to-settlement P&L for identified contracts with actual net turnover."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _date(value):
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is not None:
        raise ValueError("Ledger dates must be timezone-free calendar dates")
    return result.normalize()


def run_ledger(predictions: pd.DataFrame, prices: pd.DataFrame, sessions: pd.DataFrame,
               config: dict, model="B3", cost_ticks=1, force_roundtrip=False,
               scenario="base") -> dict:
    """Return portfolio and contract attribution from the same marks and fills.

    ``contract_daily`` has one row per held or traded contract and session. Its
    ``quantity_change`` is net turnover, whereas ``contracts_traded`` also counts
    offsetting fills in the forced-roundtrip scenario. A new position has no
    previous settlement and earns no variation margin on its entry session.
    ``contract_intervals`` allocates marks to the interval held before the
    settlement and closing/opening costs to their respective interval owners.
    """
    if not isinstance(scenario, str) or not scenario.strip():
        raise ValueError("Ledger scenario must be a nonempty string")
    required = {"event_id", "entry_date", "exit_date", "near_contract", "far_contract", "prediction"}
    if missing := required - set(predictions.columns):
        raise ValueError(f"Ledger predictions missing columns: {sorted(missing)}")
    frame = predictions.copy()
    if "model" in frame:
        frame = frame.loc[frame["model"] == model].copy()
    if "partition" in frame and frame["partition"].nunique() > 1:
        raise ValueError("Run each ledger for exactly one partition")
    if frame["event_id"].isna().any() or frame["event_id"].duplicated().any():
        raise ValueError("Ledger event IDs must be nonmissing and unique")
    threshold = float(config.get("signal_threshold", 0.075))
    multiplier = float(config.get("multiplier", 1000))
    tick = float(config.get("tick_size", 0.01))
    fee = float(config.get("fee_per_contract_side", 2.5))
    ticks = float(cost_ticks)
    if not all(math.isfinite(v) for v in (threshold, multiplier, tick, fee, ticks)) or min(threshold, fee, ticks) < 0 or min(multiplier, tick) <= 0:
        raise ValueError("Ledger cost and contract parameters are invalid")
    if not {"trade_date", "contract_id", "settlement"}.issubset(prices.columns):
        raise ValueError("Prices require trade_date, contract_id, settlement")
    if "trade_date" not in sessions:
        raise ValueError("Sessions require trade_date")
    calendar = pd.DatetimeIndex([_date(v) for v in sessions["trade_date"]]).sort_values()
    if calendar.duplicated().any():
        raise ValueError("Duplicate ledger session dates")
    price_frame = prices.copy()
    price_frame["trade_date"] = price_frame["trade_date"].map(_date)
    if price_frame["contract_id"].isna().any():
        raise ValueError("Missing price contract identifier")
    price_frame["contract_id"] = price_frame["contract_id"].astype(str)
    if price_frame.duplicated(["trade_date", "contract_id"]).any():
        raise ValueError("Duplicate settlement for an actual contract and date")
    price_frame["settlement"] = pd.to_numeric(price_frame["settlement"], errors="raise")
    lookup = price_frame.set_index(["trade_date", "contract_id"])["settlement"].to_dict()

    def settlement(day, contract):
        value = lookup.get((day, contract))
        if value is None or not np.isfinite(value):
            raise ValueError(f"Missing or nonfinite settlement for held/traded contract {contract} on {day.date()}")
        return float(value)

    for column in ("entry_date", "exit_date"):
        frame[column] = frame[column].map(_date)
    if (frame["exit_date"] <= frame["entry_date"]).any():
        raise ValueError("Ledger intervals must end after entry")
    if not frame["entry_date"].isin(calendar).all() or not frame["exit_date"].isin(calendar).all():
        raise ValueError("Ledger entry and exit dates must be exchange sessions")
    frame["prediction"] = pd.to_numeric(frame["prediction"], errors="raise")
    if not np.isfinite(frame["prediction"].to_numpy(dtype=float)).all():
        raise ValueError("Predictions must be finite")
    for column in ("near_contract", "far_contract"):
        if frame[column].isna().any():
            raise ValueError("Missing leg contract identifier")
        frame[column] = frame[column].astype(str)
    if (frame["near_contract"] == frame["far_contract"]).any():
        raise ValueError("Spread legs must be distinct actual contracts")
    frame = frame.sort_values(["entry_date", "event_id"]).reset_index(drop=True)
    if frame["entry_date"].duplicated().any():
        raise ValueError("Only one target interval can start on a session")
    if len(frame) > 1 and (frame["entry_date"].iloc[1:].to_numpy() < frame["exit_date"].iloc[:-1].to_numpy()).any():
        raise ValueError("Ledger intervals cannot overlap")

    intervals = []
    contract_intervals = {}
    entry_map = {}
    for _, event in frame.iterrows():
        forecast = float(event["prediction"])
        quantity = 1 if forecast > threshold else -1 if forecast < -threshold else 0
        row = {
            "event_id": event["event_id"], "entry_date": event["entry_date"].date().isoformat(),
            "exit_date": event["exit_date"].date().isoformat(),
            "near_contract": event["near_contract"], "far_contract": event["far_contract"],
            "prediction": forecast, "quantity": quantity, "signal_threshold": threshold,
            "exit_reason": event.get("exit_reason", "next_report"),
            "holding_sessions": int(calendar.searchsorted(event["exit_date"]) - calendar.searchsorted(event["entry_date"])),
            "gross_pnl": 0.0, "fees": 0.0, "slippage": 0.0, "net_pnl": 0.0,
            "observed_y": None if "y" not in event or pd.isna(event.get("y")) else float(event["y"]),
        }
        intervals.append(row)
        owner = len(intervals) - 1
        entry_map[event["entry_date"]] = owner
        for leg in ("near", "far"):
            contract = event[f"{leg}_contract"]
            contract_intervals[owner, contract] = {
                "event_id": event["event_id"], "scenario": scenario, "model": model,
                "cost_ticks": cost_ticks, "force_roundtrip": bool(force_roundtrip),
                "entry_date": row["entry_date"], "exit_date": row["exit_date"],
                "contract_id": contract, "leg": leg,
                "quantity": quantity if leg == "near" else -quantity,
                "gross_pnl": 0.0, "fees": 0.0, "slippage": 0.0,
                "net_pnl": 0.0, "contracts_traded": 0,
            }
    daily, trades, contract_daily = [], [], []
    positions, prior_settlements = {}, {}
    day_contracts = {}
    start_positions, start_settlements = {}, {}
    start_owner = None
    active = None
    cumulative = 0.0
    peak = 0.0

    def contract_day(day, contract):
        if contract not in day_contracts:
            old = start_positions.get(contract, 0)
            day_contracts[contract] = {
                "trade_date": day.date().isoformat(), "scenario": scenario,
                "model": model, "cost_ticks": cost_ticks,
                "force_roundtrip": bool(force_roundtrip), "contract_id": contract,
                "start_position": int(old),
                "previous_settlement": start_settlements[contract] if old else None,
                "current_settlement": settlement(day, contract),
                "gross_pnl": 0.0, "quantity_change": 0, "contracts_traded": 0,
                "slippage": 0.0, "fees": 0.0, "net_contribution": 0.0,
                "end_position": 0,
                "start_event_id": intervals[start_owner]["event_id"] if old else None,
                "end_event_id": None,
            }
        return day_contracts[contract]

    def rebalance(day, target, outgoing, incoming, phase):
        nonlocal positions
        total_fees = total_slippage = 0.0
        units = 0
        for contract in sorted(set(positions) | set(target)):
            old = positions.get(contract, 0)
            new = target.get(contract, 0)
            change = new - old
            if not change:
                continue
            mid = settlement(day, contract)
            amount = abs(change)
            closing = min(abs(old), amount) if old * change < 0 else 0
            opening = amount - closing
            charged_fee = amount * fee
            charged_slippage = amount * multiplier * tick * ticks
            for count, owner in ((closing, outgoing), (opening, incoming)):
                if count:
                    if owner is None:
                        raise AssertionError("Every charged fill must belong to an interval")
                    intervals[owner]["fees"] += count * fee
                    intervals[owner]["slippage"] += count * multiplier * tick * ticks
                    attribution = contract_intervals[owner, contract]
                    attribution["fees"] += count * fee
                    attribution["slippage"] += count * multiplier * tick * ticks
                    attribution["contracts_traded"] += int(count)
            attribution = contract_day(day, contract)
            attribution["quantity_change"] += int(change)
            attribution["contracts_traded"] += int(amount)
            attribution["fees"] += charged_fee
            attribution["slippage"] += charged_slippage
            trades.append({
                "trade_date": day.date().isoformat(), "contract_id": contract,
                "scenario": scenario, "model": model,
                "cost_ticks": cost_ticks, "force_roundtrip": bool(force_roundtrip),
                "quantity_change": int(change), "contracts_traded": int(amount),
                "old_quantity": int(old), "new_quantity": int(new),
                "side": "buy" if change > 0 else "sell", "settlement": mid,
                "execution_price": mid + np.sign(change) * tick * ticks,
                "fees": charged_fee, "slippage": charged_slippage, "phase": phase,
                "closing_quantity": int(closing), "opening_quantity": int(opening),
                "outgoing_event_id": None if outgoing is None else intervals[outgoing]["event_id"],
                "incoming_event_id": None if incoming is None else intervals[incoming]["event_id"],
            })
            total_fees += charged_fee
            total_slippage += charged_slippage
            units += amount
        positions = {k: int(v) for k, v in target.items() if v}
        return total_fees, total_slippage, units

    if len(frame):
        used_calendar = calendar[(calendar >= frame["entry_date"].min()) & (calendar <= frame["exit_date"].max())]
        for day in used_calendar:
            day_contracts = {}
            start_positions = dict(positions)
            start_settlements = dict(prior_settlements)
            start_owner = active
            gross = 0.0
            # Mark the OLD portfolio before applying today's target portfolio.
            for contract, quantity in positions.items():
                value = settlement(day, contract)
                leg_gross = quantity * multiplier * (value - prior_settlements[contract])
                gross += leg_gross
                contract_day(day, contract)["gross_pnl"] = leg_gross
                if active is None:
                    raise AssertionError("A held contract must belong to an interval")
                contract_intervals[active, contract]["gross_pnl"] += leg_gross
            if active is not None:
                intervals[active]["gross_pnl"] += gross
            elif gross:
                raise AssertionError("P&L without an active interval")
            ending = active is not None and day == pd.Timestamp(intervals[active]["exit_date"])
            incoming = entry_map.get(day)
            fees_today = slippage_today = 0.0
            traded = 0
            if incoming is not None and active is not None and not ending:
                raise ValueError("A new target cannot replace an interval before its specified exit")
            if ending or incoming is not None:
                target = {}
                if incoming is not None:
                    new_event = intervals[incoming]
                    q = new_event["quantity"]
                    target = {new_event["near_contract"]: q, new_event["far_contract"]: -q} if q else {}
                if force_roundtrip and ending and incoming is not None:
                    stages = [({}, active, None, "forced_close"), (target, None, incoming, "forced_open")]
                else:
                    stages = [(target, active, incoming, "net_rebalance" if incoming is not None else "final_or_forced_exit")]
                for wanted, outgoing, arriving, phase in stages:
                    costs = rebalance(day, wanted, outgoing, arriving, phase)
                    fees_today += costs[0]
                    slippage_today += costs[1]
                    traded += costs[2]
                active = incoming
            prior_settlements = {contract: settlement(day, contract) for contract in positions}
            net = gross - fees_today - slippage_today
            cumulative += net
            peak = max(peak, cumulative)
            daily.append({
                "trade_date": day.date().isoformat(), "gross_pnl": gross,
                "fees": fees_today, "slippage": slippage_today, "net_pnl": net,
                "cumulative_pnl": cumulative, "drawdown": cumulative - peak,
                "contracts_traded": traded, "positions": dict(positions),
            })
            for contract, attribution in sorted(day_contracts.items()):
                attribution["end_position"] = positions.get(contract, 0)
                if attribution["end_position"]:
                    attribution["end_event_id"] = intervals[active]["event_id"]
                attribution["net_contribution"] = (attribution["gross_pnl"]
                    - attribution["fees"] - attribution["slippage"])
                if attribution["start_position"] + attribution["quantity_change"] != attribution["end_position"]:
                    raise AssertionError("Contract position changes do not reconcile")
                contract_daily.append(attribution)
            for detail_column, daily_column in (("gross_pnl", "gross_pnl"), ("fees", "fees"),
                    ("slippage", "slippage"), ("net_contribution", "net_pnl"),
                    ("contracts_traded", "contracts_traded")):
                if not np.isclose(sum(r[detail_column] for r in day_contracts.values()),
                                  daily[-1][daily_column], atol=1e-7, rtol=1e-9):
                    raise AssertionError(f"Contract-day {detail_column} does not reconcile on {day.date()}")
    if positions or active is not None:
        raise AssertionError("Ledger must finish flat at the final specified exit")
    for row in intervals:
        row["net_pnl"] = row["gross_pnl"] - row["fees"] - row["slippage"]
        if row["observed_y"] is not None:
            expected_gross = row["quantity"] * multiplier * row["observed_y"]
            if not np.isclose(row["gross_pnl"], expected_gross, atol=1e-7, rtol=1e-9):
                raise ValueError(f"Observed label disagrees with held-contract P&L for {row['event_id']}")
    for row in contract_intervals.values():
        row["net_pnl"] = row["gross_pnl"] - row["fees"] - row["slippage"]
    for owner, interval in enumerate(intervals):
        legs = [contract_intervals[owner, interval[f"{leg}_contract"]] for leg in ("near", "far")]
        for column in ("gross_pnl", "fees", "slippage", "net_pnl"):
            if not np.isclose(sum(r[column] for r in legs), interval[column], atol=1e-7, rtol=1e-9):
                raise AssertionError(f"Contract-interval {column} does not reconcile for {interval['event_id']}")
    gross_total = float(sum(d["gross_pnl"] for d in daily))
    fees_total = float(sum(d["fees"] for d in daily))
    slip_total = float(sum(d["slippage"] for d in daily))
    units_total = int(sum(d["contracts_traded"] for d in daily))
    net_total = gross_total - fees_total - slip_total
    if not np.isclose(sum(r["net_pnl"] for r in intervals), net_total, atol=1e-7, rtol=1e-9):
        raise AssertionError("Interval costs and daily ledger do not reconcile")
    for contract in {row["contract_id"] for row in contract_daily}:
        days = [row for row in contract_daily if row["contract_id"] == contract]
        legs = [row for row in contract_intervals.values() if row["contract_id"] == contract]
        for daily_column, interval_column in (("gross_pnl", "gross_pnl"), ("fees", "fees"),
                ("slippage", "slippage"), ("net_contribution", "net_pnl"),
                ("contracts_traded", "contracts_traded")):
            if not np.isclose(sum(r[daily_column] for r in days), sum(r[interval_column] for r in legs),
                              atol=1e-7, rtol=1e-9):
                raise AssertionError(f"Contract {contract} daily and interval {daily_column} do not reconcile")
    active_n = sum(r["quantity"] != 0 for r in intervals)
    minimum = int(config.get("minimum_economic_intervals", 30))
    economic = "exploratory" if active_n < minimum else "positive_in_cost_scenario_not_alpha" if net_total > 0 else "no_evidence"
    roundtrips = units_total / 4.0
    yearly = []
    for year in sorted({int(row["trade_date"][:4]) for row in daily}):
        year_rows = [row for row in daily if int(row["trade_date"][:4]) == year]
        yearly.append({
            "year": year, "sessions": len(year_rows),
            "gross_pnl": sum(row["gross_pnl"] for row in year_rows),
            "fees": sum(row["fees"] for row in year_rows),
            "slippage": sum(row["slippage"] for row in year_rows),
            "net_pnl": sum(row["net_pnl"] for row in year_rows),
            "contracts_traded": sum(row["contracts_traded"] for row in year_rows),
        })
    positive_gross_days = sorted((row["gross_pnl"] for row in daily if row["gross_pnl"] > 0), reverse=True)
    positive_gross_total = sum(positive_gross_days)
    worst_day = min(daily, key=lambda row: row["net_pnl"]) if daily else None
    worst_interval = min(intervals, key=lambda row: row["net_pnl"]) if intervals else None
    summary = {
        "status": "complete" if len(frame) else "no_intervals", "model": model, "scenario": scenario,
        "n_intervals": len(intervals), "n_active_intervals": active_n,
        "n_trades": len(trades), "contracts_traded": units_total,
        "gross_pnl": gross_total, "fees": fees_total, "slippage": slip_total,
        "net_pnl": net_total, "max_drawdown": -min([0.0] + [d["drawdown"] for d in daily]),
        "roundtrip_equivalents": roundtrips,
        "break_even_roundtrip_cost_per_bbl": gross_total / (multiplier * roundtrips) if roundtrips else None,
        "cost_ticks": cost_ticks, "fee_per_contract_side": fee,
        "signal_threshold": threshold, "force_roundtrip": bool(force_roundtrip),
        "economic_evidence": economic,
        "yearly": yearly,
        "worst_day": None if worst_day is None else {"trade_date": worst_day["trade_date"], "net_pnl": worst_day["net_pnl"], "gross_pnl": worst_day["gross_pnl"]},
        "worst_interval": None if worst_interval is None else {"event_id": worst_interval["event_id"], "entry_date": worst_interval["entry_date"], "exit_date": worst_interval["exit_date"], "net_pnl": worst_interval["net_pnl"], "gross_pnl": worst_interval["gross_pnl"]},
        "flat_session_fraction": sum(not row["positions"] for row in daily) / len(daily) if daily else None,
        "flat_session_note": "Fraction of simulated sessions with no positions after the settlement rebalance, including the final close",
        "top5_positive_gross_day_share": sum(positive_gross_days[:5]) / positive_gross_total if positive_gross_total else None,
        "variation_margin_outflow": -sum(min(0.0, row["gross_pnl"]) for row in daily),
        "variation_margin_note": "Cumulative negative daily portfolio variation margin; not an initial margin, collateral, or peak liquidity requirement",
        "cost_note": "Assumed per-leg per-side adverse slippage and fees; identical signals across cost scenarios",
        "accounting_note": "Old held-contract variation margin first, then contract-level net target changes; negative prices allowed",
        "attribution_note": "Contract-day rows sum to portfolio days; contract-interval rows allocate old-position marks and closing/opening fill costs to each event; net quantity change differs from total contracts traded when offsetting fills occur",
        "attribution_reconciled": True,
        "capital_return_note": "No return on capital, margin sufficiency, or realized account performance is asserted",
    }
    return {"daily": daily, "trades": trades, "intervals": intervals,
            "contract_daily": contract_daily, "contract_intervals": list(contract_intervals.values()),
            "summary": summary}
