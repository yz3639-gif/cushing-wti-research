"""Frozen, post-review exploration of inventory and preceding spread levels.

These regressions describe an association. They are deliberately separate from
the identified-contract forecasting and accounting modules.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import chi2, norm


STATES = ("Low", "Normal", "High")
MODEL_SPECS = {
    "D1": (True, False), "D2": (True, True),
    "D1_no_year_fe": (False, False), "D2_no_year_fe": (False, True),
}


def load_mechanism_config() -> dict:
    return json.loads((Path(__file__).resolve().parents[1] / "config/mechanism.json").read_text())


def _safe(value):
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_safe(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if value is pd.NA or value is pd.NaT:
        return None
    return value


def _config(config: dict | None) -> dict:
    cfg = load_mechanism_config() if config is None else dict(config)
    required = set(load_mechanism_config())
    missing = required - set(cfg)
    if missing:
        raise ValueError(f"Mechanism configuration missing: {sorted(missing)}")
    if cfg["low_knot"] >= cfg["high_knot"]:
        raise ValueError("Low knot must be below high knot")
    if cfg["hac_kernel"] != "Bartlett" or cfg["wald_distribution"] != "chi-square":
        raise ValueError("Only the frozen Bartlett HAC / chi-square Wald convention is implemented")
    if not (0 < cfg["confidence_level"] < 1):
        raise ValueError("Confidence level must lie between zero and one")
    if cfg["bootstrap_repetitions"] < 1 or cfg["hac_lags"] < 0:
        raise ValueError("Bootstrap repetitions and HAC lags must be valid")
    if min([cfg["bootstrap_primary_block"], *cfg["bootstrap_sensitivity_blocks"]]) < 1:
        raise ValueError("Bootstrap block lengths must be positive")
    return cfg


def prepare_sample(observations: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, list[dict]]:
    """One common sample, retaining release-cycle gaps for subsequent HAC."""
    required = {"decision_date", "week_ending", "price_date", "inv_z", "spread"}
    if missing := required - set(observations.columns):
        raise ValueError(f"Mechanism observations missing columns: {sorted(missing)}")
    rows, exclusions = [], []
    data = observations.copy()
    release_dates = pd.to_datetime(data.decision_date, errors="coerce")
    valid_dates = release_dates.dropna()
    if valid_dates.duplicated().any():
        raise ValueError("Duplicate release dates are not valid independent mechanism records")
    data["_release_sort"] = release_dates
    data = data.sort_values("_release_sort", kind="stable", na_position="last")
    for period_index, (_, source) in enumerate(data.iterrows()):
        row = source.drop(labels=["_release_sort"]).to_dict()
        decision = pd.to_datetime(row["decision_date"], errors="coerce")
        week = pd.to_datetime(row["week_ending"], errors="coerce")
        quote = pd.to_datetime(row["price_date"], errors="coerce")
        reason = None
        if any(pd.isna(v) for v in (decision, week, quote)):
            reason = "invalid_date"
        elif not (pd.Timestamp(config["sample_start"]) <= decision <= pd.Timestamp(config["sample_end"])):
            reason = "outside_frozen_sample_window"
        elif quote >= decision:
            reason = "quote_not_strictly_before_release"
        elif (decision - quote).days > 7:
            reason = "quote_more_than_seven_calendar_days_old"
        elif week > decision:
            reason = "observation_week_after_release"
        try:
            z, spread = float(row["inv_z"]), float(row["spread"])
            if not np.isfinite([z, spread]).all():
                reason = reason or "nonfinite_inventory_or_spread"
        except (TypeError, ValueError):
            reason = reason or "nonnumeric_inventory_or_spread"
        if reason:
            exclusions.append({"decision_date": str(row["decision_date"]), "reason": reason,
                               "original_period_index": period_index})
            continue
        seasonal_day = date(2000, week.month, week.day).timetuple().tm_yday
        angle = 2 * np.pi * (seasonal_day - 1) / config["seasonal_period_days"]
        row.update(decision_date=decision.strftime("%Y-%m-%d"), week_ending=week.strftime("%Y-%m-%d"),
                   price_date=quote.strftime("%Y-%m-%d"), inv_z=z, spread=spread,
                   year=int(decision.year), period_index=period_index,
                   season_sin=float(np.sin(angle)), season_cos=float(np.cos(angle)),
                   state="Low" if z < config["low_knot"] else "High" if z > config["high_knot"] else "Normal")
        rows.append(row)
    return pd.DataFrame(rows), exclusions


def _design_arrays(z, sine, cosine, years, year_fe: bool, nonlinear: bool, config: dict):
    columns = ["intercept", "season_sin", "season_cos", "inv_z"]
    values = [np.ones(len(z)), sine, cosine, z]
    if year_fe:
        for year in sorted(set(years))[1:]:
            columns.append(f"year_{int(year)}")
            values.append((years == year).astype(float))
    if nonlinear:
        columns.extend(["low_hinge", "high_hinge"])
        values.extend([np.maximum(0, config["low_knot"] - z),
                       np.maximum(0, z - config["high_knot"])])
    return np.column_stack(values), columns


def design_matrix(frame: pd.DataFrame, year_fe: bool, nonlinear: bool, config: dict):
    return _design_arrays(frame.inv_z.to_numpy(float), frame.season_sin.to_numpy(float),
                          frame.season_cos.to_numpy(float), frame.year.to_numpy(int),
                          year_fe, nonlinear, config)


def _ols(X: np.ndarray, y: np.ndarray, config: dict) -> dict:
    n, k = X.shape
    if n <= k:
        return {"status": "unavailable", "reason": "nonpositive_residual_degrees_of_freedom", "n": n, "columns_n": k}
    try:
        u, singular, vt = np.linalg.svd(X, full_matrices=False)
    except np.linalg.LinAlgError:
        return {"status": "unavailable", "reason": "svd_failed", "n": n, "columns_n": k}
    rank = int(np.sum(singular > singular[0] * config["rank_relative_tolerance"]))
    condition = float(singular[0] / singular[-1]) if singular[-1] > 0 else np.inf
    metadata = {"n": n, "columns_n": k, "rank": rank, "condition_number": condition, "residual_df": n - rank}
    if rank != k:
        return {"status": "unavailable", "reason": "rank_deficient_design", **metadata}
    if condition > config["maximum_condition_number"]:
        return {"status": "unavailable", "reason": "ill_conditioned_design", **metadata}
    beta = vt.T @ ((u.T @ y) / singular)
    residual = y - X @ beta
    if not np.isfinite(beta).all() or not np.isfinite(residual).all():
        return {"status": "unavailable", "reason": "nonfinite_fit", **metadata}
    bread = (vt.T / singular ** 2) @ vt
    return {"status": "available", "beta": beta, "residual": residual, "bread": bread, **metadata}


def hac_covariance(X: np.ndarray, residual: np.ndarray, bread: np.ndarray,
                   period_indices: np.ndarray, lags: int = 8, correction: bool = True) -> np.ndarray:
    """Bartlett OLS sandwich; missing original periods do not become adjacent."""
    n, k = X.shape
    if len(set(period_indices)) != n:
        raise ValueError("HAC requires unique original time indices")
    score = X * residual[:, None]
    meat = score.T @ score
    location = {int(period): j for j, period in enumerate(period_indices)}
    for lag in range(1, lags + 1):
        right, left = [], []
        for j, period in enumerate(period_indices):
            previous = location.get(int(period) - lag)
            if previous is not None:
                right.append(j)
                left.append(previous)
        if right:
            cross = score[right].T @ score[left]
            meat += (1 - lag / (lags + 1)) * (cross + cross.T)
    covariance = bread @ meat @ bread
    if correction:
        covariance *= n / (n - k)
    return (covariance + covariance.T) / 2


def tail_support(frame: pd.DataFrame, config: dict) -> dict:
    result = {}
    for state in STATES:
        rows = frame.loc[frame.state == state]
        n, years = len(rows), sorted(rows.year.unique().tolist())
        eligible = state == "Normal" or (n >= config["tail_min_observations"] and len(years) >= config["tail_min_years"])
        result[state] = {"n": n, "years": years, "years_n": len(years), "inference_eligible": eligible,
                         "reason": None if eligible else "tail_requires_20_observations_across_3_years"}
    return result


def _slope_weights(columns: list[str]) -> dict:
    normal = np.zeros(len(columns))
    normal[columns.index("inv_z")] = 1
    low, high = normal.copy(), normal.copy()
    if "low_hinge" in columns:
        low[columns.index("low_hinge")] = -1
        high[columns.index("high_hinge")] = 1
    return dict(zip(STATES, [low, normal, high]))


def _interval(estimate: float, variance: float, config: dict) -> tuple[float | None, float | None, float | None]:
    if not np.isfinite(variance) or variance < -1e-12:
        return None, None, None
    se = float(np.sqrt(max(variance, 0.0)))
    delta = norm.ppf((1 + config["confidence_level"]) / 2) * se
    return se, float(estimate - delta), float(estimate + delta)


def joint_hinge_test(beta: np.ndarray, covariance: np.ndarray, columns: list[str],
                     support: dict, config: dict) -> dict:
    result = {"null": "low_hinge = high_hinge = 0", "distribution": "chi-square", "df": 2,
              "statistic": None, "p_value": None, "covariance": "HAC Bartlett, 8 release cycles; n/(n-k)",
              "status": "unavailable", "reason": None}
    if not all(support[s]["inference_eligible"] for s in ("Low", "High")):
        result["reason"] = "insufficient_tail_support"
        return result
    ids = [columns.index("low_hinge"), columns.index("high_hinge")]
    values, cov = beta[ids], covariance[np.ix_(ids, ids)]
    eigenvalues = np.linalg.eigvalsh(cov)
    if (not np.isfinite(cov).all() or np.linalg.matrix_rank(cov, tol=config["rank_relative_tolerance"]) < 2
            or eigenvalues.min() <= 0):
        result["reason"] = "singular_or_nonpositive_restriction_covariance"
        return result
    statistic = float(values @ np.linalg.solve(cov, values))
    result.update(status="available", statistic=statistic, p_value=float(chi2.sf(statistic, 2)))
    return result


def fit_relationship(frame: pd.DataFrame, year_fe: bool, nonlinear: bool,
                     config: dict, inference: bool = True) -> dict:
    X, columns = design_matrix(frame, year_fe, nonlinear, config)
    fit = _ols(X, frame.spread.to_numpy(float), config)
    support = tail_support(frame, config)
    result = {k: v for k, v in fit.items() if k not in {"beta", "residual", "bread"}}
    result.update(columns=columns, year_fixed_effects=year_fe, nonlinear=nonlinear,
                  tail_support=support, coefficients={}, coefficient_table=[], slopes={},
                  inference_kind="exploratory_HAC" if inference else "annual_description_only")
    if fit["status"] != "available":
        return _safe(result)
    beta, residual = fit["beta"], fit["residual"]
    covariance = hac_covariance(X, residual, fit["bread"], frame.period_index.to_numpy(int),
                                config["hac_lags"], config["hac_small_sample_correction"])
    result["coefficients"] = dict(zip(columns, beta))
    result["hac_covariance"] = covariance
    for j, column in enumerate(columns):
        tail = "Low" if column == "low_hinge" else "High" if column == "high_hinge" else None
        allowed = inference and (tail is None or support[tail]["inference_eligible"])
        se, lo, hi = _interval(beta[j], covariance[j, j], config) if allowed else (None, None, None)
        result["coefficient_table"].append({"term": column, "estimate": beta[j], "hac_std_error": se,
                                           "hac_ci_lower": lo, "hac_ci_upper": hi,
                                           "inference_status": "available" if allowed and se is not None else "withheld"})
    for state, weights in _slope_weights(columns).items():
        estimate = float(weights @ beta)
        allowed = inference and support[state]["inference_eligible"]
        se, lo, hi = _interval(estimate, weights @ covariance @ weights, config) if allowed else (None, None, None)
        result["slopes"][state] = {"estimate": estimate, "hac_std_error": se,
                                  "hac_ci_lower": lo, "hac_ci_upper": hi,
                                  "inference_status": "available" if allowed and se is not None else "withheld"}
    if nonlinear:
        result["joint_test"] = joint_hinge_test(beta, covariance, columns, support, config) if inference else {
            "status": "withheld", "reason": "annual_description_only", "statistic": None, "p_value": None, "df": 2}
    rss, tss = float(residual @ residual), float(np.sum((frame.spread - frame.spread.mean()) ** 2))
    result.update(in_sample_mae=float(np.mean(np.abs(residual))), in_sample_rmse=float(np.sqrt(rss / len(frame))),
                  in_sample_r_squared=1 - rss / tss if tss > 1e-20 else None,
                  fit_interpretation="In-sample spread-level fit; not a forecast metric")
    return _safe(result)


def moving_block_indices(n: int, length: int, rng: np.random.Generator) -> np.ndarray:
    if not (1 <= length <= n):
        raise ValueError("Moving-block length must be in [1, n]")
    starts = rng.integers(0, n - length + 1, size=int(np.ceil(n / length)))
    return (starts[:, None] + np.arange(length)).ravel()[:n]


def _percentile_summary(values: list[float], repetitions: int, allowed: bool, config: dict) -> dict:
    valid = len(values)
    alpha = (1 - config["confidence_level"]) / 2
    diagnostic = np.quantile(values, [alpha, 1 - alpha]).tolist() if values else [None, None]
    reliable = allowed and valid >= np.ceil(.95 * repetitions)
    return {"valid_repetitions": valid, "invalid_repetitions": repetitions - valid,
            "status": "available" if reliable else "unavailable",
            "reason": None if reliable else "insufficient_scope_tail_support" if not allowed else "fewer_than_95_percent_valid_replicates",
            "ci_lower": diagnostic[0] if reliable else None, "ci_upper": diagnostic[1] if reliable else None,
            "diagnostic_percentile_lower": diagnostic[0], "diagnostic_percentile_upper": diagnostic[1]}


def bootstrap_relationships(frame: pd.DataFrame, models: dict, config: dict, block_length: int) -> dict:
    repetitions = config["bootstrap_repetitions"]
    result = {"block_length": block_length, "repetitions": repetitions, "seed": config["bootstrap_seed"],
              "method": config["bootstrap_kind"], "paired_across_models": True, "models": {}, "paired_slope_differences": {}}
    if len(frame) < block_length:
        return {**result, "status": "unavailable", "reason": "sample_shorter_than_block"}
    support = tail_support(frame, config)
    values = {name: {state: [] for state in STATES} for name in MODEL_SPECS}
    deltas = {name: {state: [] for state in STATES} for name in ["D2_minus_D1", "D2_no_year_fe_minus_D1_no_year_fe"]}
    contrasts = {name: {"Low_minus_Normal": [], "High_minus_Normal": []} for name in ["D2", "D2_no_year_fe"]}
    failures = {name: Counter() for name in MODEL_SPECS}
    absent_years, missing_tail = 0, Counter()
    z, sine, cosine, years, y = (frame[col].to_numpy() for col in ("inv_z", "season_sin", "season_cos", "year", "spread"))
    original_year_count = len(np.unique(years))
    rng = np.random.default_rng(config["bootstrap_seed"])
    for _ in range(repetitions):
        idx = moving_block_indices(len(frame), block_length, rng)
        if len(np.unique(years[idx])) < original_year_count:
            absent_years += 1
        missing_tail["Low"] += int(np.sum(z[idx] < config["low_knot"]) == 0)
        missing_tail["High"] += int(np.sum(z[idx] > config["high_knot"]) == 0)
        fitted = {}
        for name, (year_fe, nonlinear) in MODEL_SPECS.items():
            X, columns = _design_arrays(z[idx], sine[idx], cosine[idx], years[idx], year_fe, nonlinear, config)
            fit = _ols(X, y[idx], config)
            if fit["status"] != "available":
                failures[name][fit["reason"]] += 1
                continue
            fitted[name] = {state: float(weights @ fit["beta"]) for state, weights in _slope_weights(columns).items()}
            for state in STATES:
                values[name][state].append(fitted[name][state])
            if nonlinear:
                contrasts[name]["Low_minus_Normal"].append(fitted[name]["Low"] - fitted[name]["Normal"])
                contrasts[name]["High_minus_Normal"].append(fitted[name]["High"] - fitted[name]["Normal"])
        for d2, d1 in [("D2", "D1"), ("D2_no_year_fe", "D1_no_year_fe")]:
            if d2 in fitted and d1 in fitted:
                for state in STATES:
                    deltas[f"{d2}_minus_{d1}"][state].append(fitted[d2][state] - fitted[d1][state])
    for name in MODEL_SPECS:
        result["models"][name] = {"failure_reasons": dict(failures[name]),
            "valid_repetitions": len(values[name]["Normal"]), "invalid_repetitions": sum(failures[name].values()),
            "slopes": {state: {"estimate": models.get(name, {}).get("slopes", {}).get(state, {}).get("estimate"),
                         **_percentile_summary(values[name][state], repetitions, support[state]["inference_eligible"], config)} for state in STATES}}
        if name in contrasts:
            result["models"][name]["slope_changes"] = {key: _percentile_summary(vals, repetitions,
                support[key.split("_")[0]]["inference_eligible"], config) for key, vals in contrasts[name].items()}
    for comparison, states in deltas.items():
        result["paired_slope_differences"][comparison] = {state: _percentile_summary(vals, repetitions,
            support[state]["inference_eligible"], config) for state, vals in states.items()}
    result.update(status="available", replicates_with_unrepresented_year=absent_years,
                  replicates_with_absent_tail=dict(missing_tail), inference_scope="Full common sample only")
    return _safe(result)


def _annual(frame: pd.DataFrame, config: dict) -> list[dict]:
    rows = []
    for year, group in frame.groupby("year", sort=True):
        has_variation = group.inv_z.nunique() > 1 and group.spread.nunique() > 1
        rows.append({"year": int(year), "n": len(group), "start": group.decision_date.min(),
                     "end": group.decision_date.max(), "partial_year": int(year) == pd.Timestamp(config["sample_end"]).year
                     and pd.Timestamp(config["sample_end"]).strftime("%m-%d") != "12-31",
                     "spearman": float(group.inv_z.corr(group.spread, method="spearman")) if has_variation else None,
                     "state_counts": {state: int((group.state == state).sum()) for state in STATES},
                     "models": {name: fit_relationship(group, False, nonlinear, config, inference=False)
                                for name, nonlinear in [("D1", False), ("D2", True)]},
                     "interpretation": "Within-year description; no annual significance test or three-year support requirement"})
    return rows


def _curves_and_records(frame: pd.DataFrame, models: dict, config: dict):
    records = frame.to_dict("records")
    grid = np.unique(np.r_[np.linspace(frame.inv_z.min(), frame.inv_z.max(), 161),
                           config["low_knot"], config["high_knot"]])
    grid = grid[(grid >= frame.inv_z.min()) & (grid <= frame.inv_z.max())]
    curves = [{"z": float(z)} for z in grid]
    for name, (year_fe, nonlinear) in MODEL_SPECS.items():
        if models[name]["status"] != "available":
            continue
        X, columns = design_matrix(frame, year_fe, nonlinear, config)
        beta = np.array([models[name]["coefficients"][col] for col in columns])
        control_ids = [j for j, col in enumerate(columns) if col not in ("inv_z", "low_hinge", "high_hinge")]
        controls = X[:, control_ids] @ beta[control_ids]
        control_mean = float(controls.mean())
        for row, fitted, control in zip(records, X @ beta, controls):
            row[f"fitted_{name}"] = float(fitted)
            if name == "D2":
                row["adjusted_spread"] = row["spread"] - float(control) + control_mean
        for curve in curves:
            estimate = control_mean + models[name]["coefficients"]["inv_z"] * curve["z"]
            if nonlinear:
                estimate += models[name]["coefficients"]["low_hinge"] * max(0, config["low_knot"] - curve["z"])
                estimate += models[name]["coefficients"]["high_hinge"] * max(0, curve["z"] - config["high_knot"])
            curve[name] = float(estimate)
    return curves, records


def analyze_mechanism(observations: pd.DataFrame, config: dict | None = None) -> dict:
    """Return JSON-safe evidence, controls, exceptions and interpretation together."""
    cfg = _config(config)
    protocol_hash = hashlib.sha256(json.dumps(cfg, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    frame, exclusions = prepare_sample(observations, cfg)
    base = {"protocol": {**cfg, "config_sha256": protocol_hash}, "status": "unavailable", "exclusions": exclusions,
            "sample": {"input_n": len(observations), "n": len(frame), "excluded_n": len(exclusions)},
            "models": {}, "main_test": {}, "bootstrap": {}, "annual": [], "leave_one_year_out": [],
            "curves": [], "records": [], "conclusion": {}}
    if frame.empty:
        base["reason"] = "no_valid_common_sample"
        return _safe(base)
    base["sample"].update(start=frame.decision_date.min(), end=frame.decision_date.max(),
        years=sorted(frame.year.unique().tolist()), tail_support=tail_support(frame, cfg),
        shared_across_models=True, quote_timing="Inventory published after matched quote; descriptive only")
    models = {name: fit_relationship(frame, year_fe, nonlinear, cfg) for name, (year_fe, nonlinear) in MODEL_SPECS.items()}
    base["models"] = models
    main_test = models["D2"].get("joint_test", {"status": "unavailable", "reason": models["D2"].get("reason"),
                                                "statistic": None, "p_value": None, "df": 2})
    base["main_test"] = {**main_test, "comparison": "D2 versus D1, common sample with year/season controls",
                         "evidence_status": cfg["evidence_status"]}
    for length in [cfg["bootstrap_primary_block"], *cfg["bootstrap_sensitivity_blocks"]]:
        base["bootstrap"][str(length)] = bootstrap_relationships(frame, models, cfg, length)
    base["annual"] = _annual(frame, cfg)
    for year in sorted(frame.year.unique()):
        remaining = frame.loc[frame.year != year]
        if remaining.empty:
            continue
        reduced = {name: fit_relationship(remaining, year_fe, nonlinear, cfg)
                   for name, (year_fe, nonlinear) in MODEL_SPECS.items()}
        base["leave_one_year_out"].append({"excluded_year": int(year), "n": len(remaining),
            "label": "Excluding 2020 stress year" if year == 2020 else f"Excluding {year}",
            "models": reduced, "main_test": reduced["D2"].get("joint_test", {
                "status": "unavailable", "reason": reduced["D2"].get("reason"), "p_value": None, "statistic": None}),
            "tail_support": tail_support(remaining, cfg),
            "hac_gap_policy": "Original release-cycle indices retained; removed-year gap not compressed"})
    base["curves"], base["records"] = _curves_and_records(frame, models, cfg)
    base["curve_interpretation"] = ("Fitted spread levels holding year and seasonal controls at common-sample means. "
        "adjusted_spread removes each row's D2 control contribution and restores its average. This is model-adjusted historical association.")
    p = main_test.get("p_value")
    available_deletions = [row["main_test"]["p_value"] for row in base["leave_one_year_out"]
                           if row["main_test"].get("p_value") is not None]
    alpha = 1 - cfg["confidence_level"]
    if p is None:
        finding = "The specified joint nonlinearity test is unavailable; its data or numerical support conditions are not met."
        category = "insufficient_evidence"
    elif p >= alpha:
        finding = "The exploratory joint test does not provide sufficient evidence of a shape difference beyond the linear controlled relationship."
        category = "insufficient_nonlinearity_evidence"
    elif len(available_deletions) < len(base["leave_one_year_out"]) or any(v >= alpha for v in available_deletions):
        finding = "The full-sample exploratory test detects a shape difference, but that conclusion is not retained in every year-deletion check."
        category = "shape_difference_sensitive_to_years"
    else:
        finding = "The exploratory shape difference is retained across the specified year-deletion checks; this does not establish stable causal or predictive value."
        category = "shape_difference_across_deletion_checks"
    base["conclusion"] = {"category": category, "finding": finding,
        "limitations": ["Analysis was specified after reviewing pooled and annual summaries.",
            "Inventory is matched to a preceding quote; the analysis is neither a release-response nor forecasting test.",
            "Year controls and short blocks cannot eliminate structural change or identify a physical causal effect.",
            "In-sample fit gains do not establish H2 or H3."]}
    base["status"] = "complete_exploration" if all(m["status"] == "available" for m in models.values()) else "partial_exploration"
    return _safe(base)
