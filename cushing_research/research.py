"""Predeclared, chronological calendar-spread prediction research.

This module never fetches prices or creates observations.  In particular, validation
predictions are development outputs, not independent evidence of model performance.
"""
from __future__ import annotations

import hashlib
from typing import Any

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler


MARKET_FEATURES = (
    "spread", "delta_spread_5", "vol_spread_20", "dte", "season_sin", "season_cos"
)
INVENTORY_FEATURES = ("inv_z", "inv_delta1", "inv_delta4")
NONLINEAR_FEATURES = ("low_hinge", "high_hinge", "inv_interaction")
NATIONAL_FEATURES = ("national_z", "national_delta1", "national_delta4")
MODEL_FEATURES = {
    "B0": (),
    "B1": MARKET_FEATURES,
    "B2": MARKET_FEATURES + INVENTORY_FEATURES,
    "B3": MARKET_FEATURES + INVENTORY_FEATURES + NONLINEAR_FEATURES,
    "N1": MARKET_FEATURES + NATIONAL_FEATURES,
    "N2": MARKET_FEATURES + NATIONAL_FEATURES + INVENTORY_FEATURES + NONLINEAR_FEATURES,
}
PRIMARY_MODELS = ("B0", "B1", "B2", "B3")
NATIONAL_PENDING_REASON = "Historical national stock definition break; lease-stock harmonization is not yet verified"


def _enabled_models(config):
    return PRIMARY_MODELS + (("N1", "N2") if config.get("national_control_enabled") is True else ())


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return None if pd.isna(value) else pd.Timestamp(value).isoformat()
    if isinstance(value, np.generic):
        return _json_value(value.item())
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def _prepare(events: pd.DataFrame, sessions: pd.DataFrame, config: dict):
    required = {
        "event_id", "decision_date", "cutoff", "entry_date", "exit_date", "y", "partition"
    } | set(MODEL_FEATURES["B3"])
    missing = sorted(required - set(events.columns))
    if missing:
        raise ValueError(f"Research events missing columns: {missing}")
    if "trade_date" not in sessions:
        raise ValueError("Sessions require trade_date")
    frame = events.copy()
    if frame["event_id"].isna().any() or frame["event_id"].duplicated().any():
        raise ValueError("Research event_id must be nonmissing and unique")
    for column in ("decision_date", "entry_date", "exit_date"):
        frame[column] = pd.to_datetime(frame[column], errors="raise").dt.normalize()
        if frame[column].isna().any() or frame[column].dt.tz is not None:
            raise ValueError(f"{column} must be a timezone-free calendar date")
    # A timezone-less cutoff is ambiguous; do not silently assume UTC.
    for value in frame["cutoff"]:
        if pd.isna(value) or pd.Timestamp(value).tzinfo is None:
            raise ValueError("Research cutoffs must carry an explicit timezone")
    frame["_cutoff"] = pd.to_datetime(frame["cutoff"], utc=True)
    frame["_year"] = frame["decision_date"].dt.year
    if (frame["exit_date"] <= frame["entry_date"]).any():
        raise ValueError("Every research label must end after entry")
    if (frame["entry_date"] <= frame["decision_date"]).any():
        raise ValueError("Execution must be after the decision date")
    numeric = list(MODEL_FEATURES["B3"]) + ["y"]
    frame[numeric] = frame[numeric].apply(pd.to_numeric, errors="raise")
    if not np.isfinite(frame[numeric].to_numpy(dtype=float)).all():
        raise ValueError("Only complete, finite eligible events may enter research")
    # Missing or definition-incompatible auxiliary data must never erase a primary event.
    # When explicitly enabled, both national models use the same complete auxiliary subset.
    for column in NATIONAL_FEATURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce") if column in frame else np.nan
    frame["_national_complete"] = np.isfinite(frame[list(NATIONAL_FEATURES)].to_numpy(dtype=float)).all(axis=1)
    dates = pd.DatetimeIndex(pd.to_datetime(sessions["trade_date"], errors="raise"))
    if dates.hasnans or dates.tz is not None or dates.duplicated().any():
        raise ValueError("Session trade dates must be unique, nonmissing calendar dates")
    dates = dates.normalize().sort_values()
    if not frame["entry_date"].isin(dates).all() or not frame["exit_date"].isin(dates).all():
        raise ValueError("Research entry and exit dates must be exchange sessions")
    frame = frame.sort_values(["decision_date", "event_id"]).reset_index(drop=True)
    if frame["decision_date"].duplicated().any():
        raise ValueError("Research accepts one event per unique decision date")
    if len(frame) > 1 and (
        frame["entry_date"].iloc[1:].to_numpy()
        < frame["exit_date"].iloc[:-1].to_numpy()
    ).any():
        raise ValueError("Primary report-cycle labels must not overlap")
    start = pd.Timestamp(config.get("train_start", "2015-01-01"))
    frame = frame.loc[frame["decision_date"] >= start].copy()
    return frame, dates


def _training_rows(frame, dates, year, config, excluded_year=None):
    refit_date = pd.Timestamp(f"{year}-01-01")
    cutoff = refit_date.tz_localize("America/New_York").tz_convert("UTC")
    gap = int(config.get("refit_gap_sessions", 5))
    if gap < 0:
        raise ValueError("refit_gap_sessions cannot be negative")
    position = dates.searchsorted(refit_date, side="left")
    if position < gap:
        return frame.iloc[:0].copy(), None, cutoff
    boundary = dates[position - gap] if gap else refit_date
    eligible = (
        (frame["_cutoff"] < cutoff)
        & (frame["exit_date"] < boundary)
        & (frame["_year"] < year)
    )
    if excluded_year is not None:
        beginning = pd.Timestamp(f"{excluded_year}-01-01")
        ending = pd.Timestamp(f"{excluded_year + 1}-01-01")
        touches_excluded_year = (
            (frame["entry_date"] < ending) & (frame["exit_date"] >= beginning)
        ) | (frame["_year"] == excluded_year)
        eligible &= ~touches_excluded_year
    return frame.loc[eligible].copy(), boundary, cutoff


def _fit_predict(train, target, features, alpha):
    """Minimize summed squared residual + alpha * squared standardized slopes."""
    scaler = StandardScaler()
    x_train = scaler.fit_transform(train[list(features)].to_numpy(dtype=float))
    estimator = Ridge(alpha=float(alpha), fit_intercept=True, solver="svd")
    estimator.fit(x_train, train["y"].to_numpy(dtype=float))
    prediction = estimator.predict(scaler.transform(target[list(features)].to_numpy(dtype=float)))
    return prediction, estimator, scaler


def _walkforward(frame, dates, model, alpha, years, config, excluded_year=None):
    predictions, coefficients, audits = [], [], []
    recent_years = set(config.get("recent_years", [2026]))
    freeze_year = min(recent_years) if recent_years else None
    features = MODEL_FEATURES[model]
    for year in years:
        target = frame.loc[frame["_year"] == year]
        if excluded_year == year:
            continue
        if excluded_year is not None:
            beginning = pd.Timestamp(f"{excluded_year}-01-01")
            ending = pd.Timestamp(f"{excluded_year + 1}-01-01")
            target = target.loc[~((target["entry_date"] < ending) & (target["exit_date"] >= beginning))]
        if target.empty:
            continue
        fit_year = freeze_year if year in recent_years else year
        train, boundary, cutoff = _training_rows(frame, dates, fit_year, config, excluded_year)
        audit = {
            "model": model, "prediction_year": year, "fit_year": fit_year,
            "refit_cutoff": cutoff.isoformat(),
            "gap_boundary": None if boundary is None else boundary.date().isoformat(),
            "training_n": len(train), "excluded_year": excluded_year,
            "max_training_label_end": None if train.empty else train["exit_date"].max().date().isoformat(),
        }
        if model == "B0":
            values = np.zeros(len(target))
        else:
            minimum = int(config.get("minimum_training_observations", max(30, 2 * len(features))))
            if len(train) < minimum:
                audits.append({**audit, "status": "insufficient_training"})
                continue
            values, estimator, scaler = _fit_predict(train, target, features, alpha)
            original_coefs = estimator.coef_ / scaler.scale_
            original_intercept = estimator.intercept_ - np.dot(original_coefs, scaler.mean_)
            for feature, raw, standardized, mean, scale in zip(
                features, original_coefs, estimator.coef_, scaler.mean_, scaler.scale_
            ):
                coefficients.append({
                    **audit, "alpha": alpha, "feature": feature,
                    "coefficient": float(raw), "standardized_coefficient": float(standardized),
                    "training_mean": float(mean), "training_scale": float(scale),
                })
            coefficients.append({
                **audit, "alpha": alpha, "feature": "__intercept__",
                "coefficient": float(original_intercept),
                "standardized_coefficient": float(estimator.intercept_),
                "training_mean": None, "training_scale": None,
            })
        audits.append({**audit, "status": "fitted" if model != "B0" else "zero_baseline"})
        for (_, row), value in zip(target.iterrows(), values):
            record = {k: v for k, v in row.items() if not k.startswith("_")}
            for key in ("decision_date", "entry_date", "exit_date"):
                record[key] = row[key].date().isoformat()
            record.update({
                "model": model, "prediction": float(value), "alpha": alpha,
                "fit_year": fit_year, "training_n": len(train),
                "evaluation_role": "development_selected" if year in config.get("validation_years", [2019, 2020, 2021]) else "chronological_out_of_sample",
            })
            predictions.append(_json_value(record))
    return predictions, coefficients, audits


def _validation_rows(frame, years, excluded_year=None):
    eligible = frame.loc[frame["_year"].isin(years)].copy()
    if excluded_year is not None:
        beginning = pd.Timestamp(f"{excluded_year}-01-01")
        ending = pd.Timestamp(f"{excluded_year + 1}-01-01")
        touches = ((eligible["entry_date"] < ending) & (eligible["exit_date"] >= beginning))
        eligible = eligible.loc[(eligible["_year"] != excluded_year) & ~touches]
    return eligible


def _validation_candidate_audit(expected, prediction_rows, years, model, alpha, fit_audit):
    produced = pd.DataFrame(prediction_rows)
    actual_ids = set(produced["event_id"]) if len(produced) else set()
    duplicated_ids = sorted(set(produced.loc[produced["event_id"].duplicated(keep=False), "event_id"])) if len(produced) else []
    expected_ids = set(expected["event_id"])
    annual = []
    for year in years:
        target_ids = set(expected.loc[expected["_year"] == year, "event_id"])
        year_rows = [row for row in prediction_rows if pd.Timestamp(row["decision_date"]).year == year]
        year_ids = {row["event_id"] for row in year_rows}
        annual.append({
            "year": int(year), "expected_eligible_n": len(target_ids), "predicted_n": len(year_rows),
            "missing_prediction_n": len(target_ids - year_ids),
            "status": "missing_validation_year" if not target_ids else "complete" if year_ids == target_ids and len(year_rows) == len(target_ids) else "incomplete_predictions",
        })
    missing_years = [row["year"] for row in annual if row["expected_eligible_n"] == 0 or row["predicted_n"] == 0]
    complete = bool(years) and not missing_years and not duplicated_ids and actual_ids == expected_ids
    return {
        "model": model, "alpha": alpha, "status": "complete" if complete else "incomplete",
        "expected_years": list(years), "missing_years": missing_years,
        "actual_prediction_years": sorted({pd.Timestamp(row["decision_date"]).year for row in prediction_rows}),
        "expected_eligible_n": len(expected_ids), "predicted_n": len(produced),
        "duplicate_event_ids": duplicated_ids,
        "missing_event_ids": sorted(expected_ids - actual_ids), "unexpected_event_ids": sorted(actual_ids - expected_ids),
        "sample_sha256": hashlib.sha256("\n".join(sorted(row["event_id"] for row in prediction_rows)).encode()).hexdigest(),
        "by_year": annual, "fit_audit": fit_audit,
    }


def _run_variant(frame, dates, config, excluded_year=None):
    val_years = list(config.get("validation_years", [2019, 2020, 2021]))
    if not val_years or len(set(val_years)) != len(val_years):
        raise ValueError("Validation years must be nonempty and unique")
    expected_val_years = [year for year in val_years if year != excluded_year]
    if not expected_val_years:
        raise ValueError("Variant must retain at least one expected validation year")
    eval_years = list(config.get("test_years", [2022, 2023, 2024, 2025])) + list(config.get("recent_years", [2026]))
    alphas = sorted({float(a) for a in config.get("alphas", [0.1, 1, 10, 100])})
    if not alphas or any(not np.isfinite(a) or a <= 0 for a in alphas):
        raise ValueError("Ridge penalties must be finite and strictly positive")
    predictions, tuning, coefficients, audits, unavailable = [], [], [], [], []
    validation_audits, model_samples = [], {}
    expected_primary = _validation_rows(frame, expected_val_years, excluded_year)
    coverage = {
        "expected_years": expected_val_years,
        "variant_excluded_years": [excluded_year] if excluded_year in val_years else [],
        "input_year_counts": [{
            "year": year, "input_n": int(frame["_year"].eq(year).sum()),
            "eligible_n": int(expected_primary["_year"].eq(year).sum()),
            "excluded_by_variant_n": int(frame["_year"].eq(year).sum() - expected_primary["_year"].eq(year).sum()),
            "required": year in expected_val_years,
        } for year in val_years],
        "sample_scope": "Counts describe eligible events supplied to research; upstream source, feature and boundary exclusions are recorded in excluded_events.csv",
        "minimum_pooled_observations": int(config.get("minimum_validation_observations", 26)),
    }
    selected = {"B0": None}
    base = _walkforward(frame, dates, "B0", None, val_years + eval_years, config, excluded_year)
    predictions.extend(base[0])
    audits.extend(base[2])
    for model in _enabled_models(config):
        if model == "B0":
            continue
        model_frame = frame.loc[frame["_national_complete"]] if model.startswith("N") else frame
        expected_validation = _validation_rows(model_frame, expected_val_years, excluded_year)
        candidates = []
        cache = {}
        candidate_audits = []
        for alpha in alphas:
            result = _walkforward(model_frame, dates, model, alpha, val_years, config, excluded_year)
            cache[alpha] = result
            vals = pd.DataFrame(result[0])
            mae = float(np.abs(vals["y"] - vals["prediction"]).mean()) if not vals.empty else np.nan
            n = len(vals)
            candidates.append((mae, alpha, n))
            candidate_audit = _validation_candidate_audit(expected_validation, result[0], expected_val_years, model, alpha, result[2])
            candidate_audits.append(candidate_audit)
            validation_audits.append(candidate_audit)
            tuning.append({
                "model": model, "alpha": alpha, "scope": "validation_pooled",
                "year": None, "n": n, "mae": mae, "selected": False,
                "excluded_year": excluded_year,
                "coverage_status": candidate_audit["status"],
                "missing_years": candidate_audit["missing_years"],
                "sample_sha256": candidate_audit["sample_sha256"],
            })
            for year in expected_val_years:
                group = vals.loc[pd.to_datetime(vals["decision_date"]).dt.year == year] if len(vals) else vals
                annual = next(row for row in candidate_audit["by_year"] if row["year"] == year)
                tuning.append({
                    "model": model, "alpha": alpha, "scope": "validation_year",
                    "year": int(year), "n": len(group),
                    "expected_eligible_n": annual["expected_eligible_n"],
                    "coverage_status": annual["status"],
                    "mae": float(np.abs(group["y"] - group["prediction"]).mean()) if len(group) else None,
                    "selected": False, "excluded_year": excluded_year,
                })
        same_sample = len({row["sample_sha256"] for row in candidate_audits}) == 1
        model_samples[model] = same_sample
        if not same_sample:
            unavailable.append({"model": model, "reason": "validation_candidate_sample_mismatch"})
            continue
        if not all(row["status"] == "complete" for row in candidate_audits):
            missing_years = sorted({year for row in candidate_audits for year in row["missing_years"]})
            unavailable.append({"model": model, "reason": "incomplete_validation_year_coverage" if missing_years else "incomplete_validation_predictions",
                                "expected_years": expected_val_years, "missing_years": missing_years})
            continue
        minimum_val = int(config.get("minimum_validation_observations", 26))
        valid = [x for x in candidates if np.isfinite(x[0]) and x[2] >= minimum_val]
        if not valid:
            unavailable.append({"model": model, "reason": "insufficient_validation_history"})
            continue
        best_mae = min(x[0] for x in valid)
        chosen = max(x[1] for x in valid if x[0] <= best_mae + 1e-12)
        selected[model] = chosen
        for row in tuning:
            if row["model"] == model:
                row["selected"] = row["alpha"] == chosen
        val_result = cache[chosen]
        eval_result = _walkforward(model_frame, dates, model, chosen, eval_years, config, excluded_year)
        predictions.extend(val_result[0] + eval_result[0])
        coefficients.extend(val_result[1] + eval_result[1])
        audits.extend(val_result[2] + eval_result[2])
    primary_audits = [row for row in validation_audits if row["model"] in PRIMARY_MODELS]
    coverage.update(
        status="complete" if all(model in selected for model in PRIMARY_MODELS) else "incomplete",
        candidate_audits=validation_audits,
        same_sample_across_alphas=model_samples,
        primary_common_candidate_sample=bool(primary_audits) and len({row["sample_sha256"] for row in primary_audits}) == 1,
        selected_year_counts={
            model: next(row["by_year"] for row in validation_audits if row["model"] == model and row["alpha"] == alpha)
            for model, alpha in selected.items() if model != "B0"
        },
    )
    return predictions, tuning, coefficients, audits, selected, unavailable, coverage


def _metrics(predictions):
    if predictions.empty:
        return []
    records = []
    predictions = predictions.copy()
    predictions["_year"] = pd.to_datetime(predictions["decision_date"]).dt.year
    predictions["_state"] = np.where(predictions["inv_z"] < -1, "Low", np.where(predictions["inv_z"] > 1, "High", "Normal"))
    for (partition, model), all_rows in predictions.groupby(["partition", "model"]):
        groups = [("overall", None, None, all_rows)] + [
            ("year", int(year), None, group) for year, group in all_rows.groupby("_year")
        ] + [("state", None, str(state), group) for state, group in all_rows.groupby("_state")]
        for scope, year, state, group in groups:
            residual = group["y"].to_numpy() - group["prediction"].to_numpy()
            directional = group.loc[(group["y"] != 0) & (group["prediction"] != 0)]
            benchmark = predictions.loc[
                (predictions["model"] == "B1") & (predictions["partition"] == partition),
                ["event_id", "prediction"],
            ].rename(columns={"prediction": "benchmark"})
            paired = group.merge(benchmark, on="event_id", how="inner", validate="one_to_one")
            denominator = float(((paired["y"] - paired["benchmark"]) ** 2).sum())
            r2 = 1 - float(((paired["y"] - paired["prediction"]) ** 2).sum()) / denominator if denominator > 0 else None
            baseline_mae = float(np.abs(paired["y"] - paired["benchmark"]).mean()) if len(paired) else None
            paired_mae = float(np.abs(paired["y"] - paired["prediction"]).mean()) if len(paired) else None
            records.append({
                "partition": str(partition), "model": model, "scope": scope, "year": year, "state": state,
                "n": len(group), "mae": float(np.mean(np.abs(residual))),
                "rmse": float(np.sqrt(np.mean(residual ** 2))),
                "B1_mae": baseline_mae,
                "mae_improvement_vs_B1": baseline_mae - paired_mae if baseline_mae is not None else None,
                "mae_improvement_fraction_vs_B1": (baseline_mae - paired_mae) / baseline_mae if baseline_mae is not None and baseline_mae > 0 else None,
                "direction_accuracy": float((np.sign(directional["prediction"]) == np.sign(directional["y"])).mean()) if len(directional) else None,
                "direction_n": len(directional), "oos_r2_vs_B1": r2,
                "oos_r2_n": len(paired),
            })
    return records


def paired_mae_bootstrap(predictions, candidate="B3", baseline="B1", block_size=8,
                         reps=2000, seed=4912816):
    """Paired moving-block CI of baseline MAE minus candidate MAE."""
    if int(block_size) < 1 or int(reps) < 1:
        raise ValueError("Bootstrap block size and repetitions must be positive")
    columns = ["event_id", "decision_date", "y", "prediction"]
    left = predictions.loc[predictions["model"] == candidate, columns]
    right = predictions.loc[predictions["model"] == baseline, ["event_id", "y", "prediction"]]
    pair = left.merge(right, on="event_id", suffixes=("_candidate", "_baseline"), validate="one_to_one")
    pair = pair.sort_values(["decision_date", "event_id"])
    if len(pair) and not np.allclose(pair["y_candidate"], pair["y_baseline"], rtol=0, atol=1e-10):
        raise ValueError("Paired models must have identical observed targets")
    n = len(pair)
    result = {
        "comparison": f"{candidate}_vs_{baseline}", "candidate": candidate,
        "baseline": baseline, "block_size": int(block_size), "reps": int(reps), "n": n,
        "mae": None, "baseline_mae": None, "mae_improvement": None,
        "ci_low": None, "ci_high": None, "evidence": "exploratory",
        "exploratory": n < max(52, 8 * int(block_size)),
    }
    if not n:
        return result
    loss_c = np.abs(pair["y_candidate"].to_numpy() - pair["prediction_candidate"].to_numpy())
    loss_b = np.abs(pair["y_baseline"].to_numpy() - pair["prediction_baseline"].to_numpy())
    difference = loss_b - loss_c
    result.update(mae=float(loss_c.mean()), baseline_mae=float(loss_b.mean()), mae_improvement=float(difference.mean()))
    if n < max(8, 2 * int(block_size)):
        result["ci_note"] = "Too few paired events for a meaningful block-bootstrap interval"
        return result
    rng = np.random.default_rng(int(seed))
    blocks_per_sample = int(np.ceil(n / block_size))
    starts = rng.integers(0, n - block_size + 1, size=(int(reps), blocks_per_sample))
    indices = (starts[:, :, None] + np.arange(block_size)).reshape(int(reps), -1)[:, :n]
    means = difference[indices].mean(axis=1)
    low, high = np.quantile(means, [0.025, 0.975])
    result.update(ci_low=float(low), ci_high=float(high))
    if not result["exploratory"]:
        result["evidence"] = "improvement" if low > 0 else "worse" if high < 0 else "no_clear_improvement"
    return result


def run_research(events: pd.DataFrame, sessions: pd.DataFrame, config: dict) -> dict:
    frame, dates = _prepare(events, sessions, config)
    result = _run_variant(frame, dates, config)
    rows, tuning, coefficients, audits, selected, unavailable, validation_coverage = result
    predictions = pd.DataFrame(rows)
    expected_test_ids = set(frame.loc[frame["partition"] == "test", "event_id"])
    test_model_ids = {
        model: {row["event_id"] for row in rows if row["model"] == model and row["partition"] == "test"}
        for model in PRIMARY_MODELS
    }
    paired_test_complete = bool(expected_test_ids) and all(ids == expected_test_ids for ids in test_model_ids.values())
    expected_test_years = sorted(int(year) for year in config.get('test_years', [2022, 2023, 2024, 2025]))
    actual_test_year_counts = {int(year): int(n) for year,n in frame.loc[frame['partition']=='test'].groupby(
        pd.to_datetime(frame.loc[frame['partition']=='test','decision_date']).dt.year).size().items()}
    missing_test_years = sorted(set(expected_test_years)-set(actual_test_year_counts))
    national_enabled = config.get("national_control_enabled") is True
    national_test_ids = set(frame.loc[(frame["partition"] == "test") & frame["_national_complete"], "event_id"])
    national_produced_ids = {
        model: {row["event_id"] for row in rows if row["model"] == model and row["partition"] == "test"}
        for model in ("N1", "N2")
    }
    national_complete = bool(national_test_ids) and all(ids == national_test_ids for ids in national_produced_ids.values())
    comparisons, robustness = [], []
    block = int(config.get("bootstrap_block", 8))
    reps = int(config.get("bootstrap_reps", 2000))
    seed = int(config.get("bootstrap_seed", 4912816))
    if not predictions.empty:
        for partition in ("test", "recent"):
            subset = predictions.loc[predictions["partition"] == partition]
            if subset.empty:
                continue
            pairs = [("B3", "B1"), ("B2", "B1"), ("B3", "B2")]
            if national_enabled and {"N1", "N2"}.issubset(set(subset["model"])):
                pairs.append(("N2", "N1"))
            for candidate, baseline in pairs:
                sizes = [block, 4, 13] if (candidate, baseline) == ("B3", "B1") else [block]
                for size in dict.fromkeys(sizes):
                    comparison = paired_mae_bootstrap(subset, candidate, baseline, size, reps, seed)
                    comparison.update(partition=partition, primary=(candidate == "B3" and baseline == "B1" and size == block))
                    comparisons.append(comparison)
                    if size != block:
                        robustness.append({"check": f"bootstrap_block_{size}", "model": candidate, **comparison})
                    elif candidate == "N2":
                        robustness.append({"check": "national_inventory_control", "model": candidate, **comparison})
        subset = predictions.loc[
            (predictions["partition"] == "test")
            & pd.to_datetime(predictions["decision_date"]).dt.year.between(2023, 2025)
        ]
        comparison = paired_mae_bootstrap(subset, "B3", "B1", block, reps, seed)
        robustness.append({"check": "test_2023_2025", "partition": "test", "model": "B3", **comparison})
    excluded = _run_variant(frame, dates, config, excluded_year=2020)
    excluded_predictions = pd.DataFrame(excluded[0])
    if not excluded_predictions.empty:
        for partition in ("test", "recent"):
            subset = excluded_predictions.loc[excluded_predictions["partition"] == partition]
            if subset.empty:
                continue
            pairs = [("B3", "B1"), ("B2", "B1")]
            if national_enabled and {"N1", "N2"}.issubset(set(subset["model"])):
                pairs.append(("N2", "N1"))
            for candidate, baseline in pairs:
                comparison = paired_mae_bootstrap(subset, candidate, baseline, block, reps, seed)
                robustness.append({
                    "check": "exclude_2020_retrain_retune", "partition": partition,
                    "model": candidate, "selected_alphas": excluded[4], **comparison,
                })
    metadata = {
        "status": "incomplete" if not paired_test_complete or missing_test_years else "insufficient_history" if any(row["model"] in PRIMARY_MODELS for row in unavailable) else "complete",
        "research_observations": len(frame), "selected_alphas": selected,
        "paired_test_events_complete": paired_test_complete,
        "expected_test_event_count": len(expected_test_ids),
        "test_model_event_counts": {model: len(ids) for model, ids in test_model_ids.items()},
        "test_year_coverage": {"expected_years": expected_test_years, "actual_event_counts": actual_test_year_counts,
                               "missing_years": missing_test_years, "complete": not missing_test_years},
        "test_event_mismatches": {
            model: {"missing_event_ids": sorted(expected_test_ids - ids), "unexpected_event_ids": sorted(ids - expected_test_ids)}
            for model, ids in test_model_ids.items() if ids != expected_test_ids
        },
        "unavailable_models": unavailable, "model_features": MODEL_FEATURES,
        "validation_coverage": validation_coverage,
        "enabled_models": _enabled_models(config),
        "national_control_enabled": national_enabled,
        "national_control": {
            "enabled": national_enabled,
            "status": "pending" if not national_enabled else "complete" if national_complete else "insufficient_history",
            "pending_reason": NATIONAL_PENDING_REASON if not national_enabled else None,
            "complete_feature_observations": int(frame["_national_complete"].sum()),
            "missing_feature_observations": int((~frame["_national_complete"]).sum()),
            "expected_test_event_count": len(national_test_ids) if national_enabled else None,
            "test_model_event_counts": {model: len(ids) for model, ids in national_produced_ids.items()} if national_enabled else {},
            "sample_rule": "When enabled, N1 and N2 use a common complete national-feature subset; primary B0-B3 events are unchanged",
        },
        "ridge_objective": "sum((y - intercept - standardized_X @ beta)**2) + alpha * sum(beta**2)",
        "sklearn_alpha_conversion": "sklearn Ridge alpha = research alpha; intercept unpenalized",
        "primary_comparison": "B3_vs_B1", "primary_metric": "MAE",
        "positive_improvement_means": "baseline MAE minus candidate MAE",
        "refit_rule": "Annual January 1 expanding fit; completed labels strictly before the five-session gap boundary",
        "recent_rule": "Coefficients frozen at January 1 of the first recent year; recent outcomes never enter that fit",
        "hyperparameter_rule": f"Expected validation years {validation_coverage['expected_years']}: annual walkforward pooled MAE on identical candidate samples; every expected year must be present; ties prefer larger alpha; never tune P&L",
        "validation_note": "Validation predictions use development-selected penalties and are not independent final evidence",
        "bootstrap_note": "Paired consecutive-event moving blocks on fixed forecasts; conditional forecast-loss uncertainty, not model-selection uncertainty",
        "exploratory_rule": "Fewer than max(52, eight blocks) paired events is exploratory; fewer than two blocks has no CI",
        "fit_audit": audits,
        "exclude_2020_fit_audit": excluded[3],
        "exclude_2020_tuning": excluded[1],
        "exclude_2020_validation_coverage": excluded[6],
        "exclude_2020_unavailable_models": excluded[5],
        "exclude_2020_rule": "Remove decision-year 2020 and labels whose entry-exit interval touches 2020; retune only remaining development observations",
    }
    return _json_value({
        "predictions": rows, "metrics": _metrics(predictions), "comparisons": comparisons,
        "tuning": tuning, "coefficients": coefficients, "robustness": robustness,
        "metadata": metadata,
    })
