"""Numerical and evidence-identity checks for the exploratory extension."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest
from scipy.stats import chi2
from sklearn.linear_model import LinearRegression

from cushing_research.mechanism import (
    _ols, analyze_mechanism, bootstrap_relationships, design_matrix,
    fit_relationship, hac_covariance, joint_hinge_test, load_mechanism_config,
    moving_block_indices, prepare_sample, tail_support,
)


def fixture_data(n=430, seed=90210):
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2015-01-07", periods=n, freq="7D")
    z = rng.normal(0, 1.8, n)
    residual = np.zeros(n)
    noise = rng.normal(0, .06, n)
    for i in range(1, n):
        residual[i] = .7 * residual[i-1] + noise[i]
    level = .25 - .4 * z + .65 * np.maximum(0, -1-z) - .2 * np.maximum(0, z-1)
    level += .1 * (dates.year - 2015) + residual
    return pd.DataFrame({"decision_date": dates.strftime("%Y-%m-%d"),
        "week_ending": (dates-pd.Timedelta(days=5)).strftime("%Y-%m-%d"),
        "price_date": (dates-pd.Timedelta(days=1)).strftime("%Y-%m-%d"), "inv_z": z, "spread": level})


@pytest.fixture
def cfg():
    config = load_mechanism_config()
    config["bootstrap_repetitions"] = 24
    return config


def test_ols_recovers_exact_nonzero_piecewise_slopes(cfg):
    frame, _ = prepare_sample(fixture_data(), cfg)
    X, columns = design_matrix(frame, True, True, cfg)
    beta = np.arange(X.shape[1]) / 19 - .25
    beta[columns.index("inv_z")] = -.3
    beta[columns.index("low_hinge")] = .7
    beta[columns.index("high_hinge")] = -.2
    frame["spread"] = X @ beta
    result = fit_relationship(frame, True, True, cfg)
    np.testing.assert_allclose([result["coefficients"][c] for c in columns], beta, atol=1e-12)
    np.testing.assert_allclose([result["slopes"][s]["estimate"] for s in ("Low", "Normal", "High")], [-1., -.3, -.5], atol=1e-12)
    assert result["in_sample_r_squared"] == pytest.approx(1)


def test_ols_coefficients_and_predictions_match_independent_library(cfg):
    frame, _ = prepare_sample(fixture_data(), cfg)
    X, columns = design_matrix(frame, True, True, cfg)
    ours = _ols(X, frame.spread.to_numpy(), cfg)
    reference = LinearRegression(fit_intercept=False).fit(X, frame.spread.to_numpy())
    np.testing.assert_allclose(ours["beta"], reference.coef_, atol=1e-12, rtol=1e-11)
    np.testing.assert_allclose(X @ ours["beta"], reference.predict(X), atol=1e-12)


def test_hac_matches_independent_double_sum_and_small_sample_correction(cfg):
    rng = np.random.default_rng(71)
    X = np.column_stack([np.ones(60), rng.normal(size=60), rng.normal(size=60)])
    y = rng.normal(size=60)
    fit = _ols(X, y, cfg)
    indices = np.r_[np.arange(30), np.arange(40, 70)]
    scores = X * fit["residual"][:, None]
    meat = np.zeros((3, 3))
    for t in range(60):
        for s in range(60):
            lag = abs(indices[t] - indices[s])
            if lag <= 8:
                meat += (1-lag/9) * np.outer(scores[t], scores[s])
    bread = np.linalg.inv(X.T @ X)
    expected = bread @ meat @ bread * 60/57
    actual = hac_covariance(X, fit["residual"], fit["bread"], indices, 8, True)
    np.testing.assert_allclose(actual, expected, rtol=1e-11, atol=1e-13)
    uncorrected = hac_covariance(X, fit["residual"], fit["bread"], indices, 8, False)
    np.testing.assert_allclose(actual, uncorrected * 60/57, atol=1e-13)


def test_hac_does_not_join_deleted_year_boundary(cfg):
    X = np.column_stack([np.ones(8), np.arange(8)])
    residual = np.array([1, -1, 2, -2, .5, -.5, 3, -3])
    bread = np.linalg.inv(X.T @ X)
    gapped = hac_covariance(X, residual, bread, np.r_[np.arange(4), np.arange(100, 104)], 2)
    compressed = hac_covariance(X, residual, bread, np.arange(8), 2)
    assert not np.allclose(gapped, compressed)
    with pytest.raises(ValueError, match="unique"):
        hac_covariance(X, residual, bread, np.repeat(np.arange(4), 2), 2)


def test_wald_matches_known_two_restriction_quadratic(cfg):
    beta = np.array([2., 1., -2.])
    cov = np.diag([100., .25, 1.])
    support = {s: {"inference_eligible": True} for s in ("Low", "High")}
    result = joint_hinge_test(beta, cov, ["intercept", "low_hinge", "high_hinge"], support, cfg)
    assert result["statistic"] == pytest.approx(8)
    assert result["p_value"] == pytest.approx(chi2.sf(8, 2))
    cov[2, 2] = 0
    assert joint_hinge_test(beta, cov, ["intercept", "low_hinge", "high_hinge"], support, cfg)["status"] == "unavailable"


def test_common_sample_rejects_bad_time_and_nonfinite_value(cfg):
    inputs = fixture_data(100)
    inputs.loc[2, "price_date"] = inputs.loc[2, "decision_date"]
    inputs.loc[3, "spread"] = np.inf
    inputs.loc[4, "week_ending"] = "bad-date"
    inputs.loc[5, "price_date"] = "2015-01-01"
    frame, exclusions = prepare_sample(inputs, cfg)
    assert len(frame) == 96
    assert len(exclusions) == 4
    assert {r["reason"] for r in exclusions} == {
        "quote_not_strictly_before_release", "nonfinite_inventory_or_spread", "invalid_date",
        "quote_more_than_seven_calendar_days_old"}
    assert set(frame.period_index) == set(range(100)) - {2, 3, 4, 5}
    inputs.loc[1, "decision_date"] = inputs.loc[0, "decision_date"]
    with pytest.raises(ValueError, match="Duplicate"):
        prepare_sample(inputs, cfg)


def test_tail_support_withholds_inference_but_keeps_annual_description(cfg):
    frame, _ = prepare_sample(fixture_data(52), cfg)
    result = fit_relationship(frame, False, True, cfg)
    assert result["status"] == "available"
    assert result["joint_test"]["reason"] == "insufficient_tail_support"
    assert result["slopes"]["Low"]["estimate"] is not None
    assert result["slopes"]["Low"]["hac_ci_lower"] is None
    annual = fit_relationship(frame, False, True, cfg, inference=False)
    assert annual["slopes"]["Low"]["estimate"] is not None
    assert annual["joint_test"]["reason"] == "annual_description_only"
    assert all(c["hac_ci_lower"] is None for c in annual["coefficient_table"])


def test_boundary_belongs_to_normal_and_missing_tail_fit_is_reported(cfg):
    inputs = fixture_data(100)
    inputs.loc[:1, "inv_z"] = [-1, 1]
    inputs.loc[2:, "inv_z"] = np.linspace(-.9, .9, 98)
    frame, _ = prepare_sample(inputs, cfg)
    assert (frame.state == "Normal").all()
    result = fit_relationship(frame, False, True, cfg)
    assert result["status"] == "unavailable"
    assert result["reason"] == "rank_deficient_design"


def test_moving_blocks_are_non_circular_and_preserve_order():
    index = moving_block_indices(21, 8, np.random.default_rng(23))
    assert len(index) == 21
    assert index.min() >= 0 and index.max() < 21
    assert np.all(np.diff(index[:8]) == 1)
    assert np.all(np.diff(index[8:16]) == 1)
    assert np.all(np.diff(index[16:]) == 1)
    np.testing.assert_array_equal(index, moving_block_indices(21, 8, np.random.default_rng(23)))


def test_bootstrap_refits_and_pairs_all_models(cfg):
    frame, _ = prepare_sample(fixture_data(), cfg)
    models = {name: fit_relationship(frame, year, nonlinear, cfg) for name, year, nonlinear in [
        ("D1", True, False), ("D2", True, True), ("D1_no_year_fe", False, False), ("D2_no_year_fe", False, True)]}
    result = bootstrap_relationships(frame, models, cfg, 8)
    again = bootstrap_relationships(frame, models, cfg, 8)
    assert result == again
    assert result["paired_across_models"]
    for model in result["models"].values():
        assert model["valid_repetitions"] + model["invalid_repetitions"] == 24
        assert model["slopes"]["Normal"]["ci_upper"] > model["slopes"]["Normal"]["ci_lower"]
    paired = result["paired_slope_differences"]["D2_minus_D1"]["Low"]
    assert paired["valid_repetitions"] == 24
    assert paired["ci_upper"] < 0


def test_full_result_identity_curve_adjustment_and_all_deletions(cfg):
    original = fixture_data()
    result = analyze_mechanism(original, cfg)
    assert result["status"] == "complete_exploration"
    assert result["sample"]["n"] == len(original)
    assert set(result["bootstrap"]) == {"8", "4", "13"}
    assert set(r["excluded_year"] for r in result["leave_one_year_out"]) == set(range(2015, 2024))
    assert all(m["n"] == len(original) for m in result["models"].values())
    records = pd.DataFrame(result["records"])
    assert records.adjusted_spread.mean() == pytest.approx(records.spread.mean())
    curves = result["curves"]
    assert all(set(c) == {"z", "D1", "D2", "D1_no_year_fe", "D2_no_year_fe"} for c in curves)
    assert "post-review" in result["protocol"]["evidence_status"]
    assert "not preregistered" in result["protocol"]["evidence_status"]
    json.dumps(result, allow_nan=False)
    assert "forecast" in result["conclusion"]["limitations"][1]


def test_input_order_does_not_change_estimates_or_bootstrap(cfg):
    original = fixture_data(170)
    result = analyze_mechanism(original, cfg)
    shuffled = analyze_mechanism(original.sample(frac=1, random_state=5), cfg)
    assert result == shuffled


def test_numerical_degeneracy_is_visible_in_bootstrap(cfg):
    inputs = fixture_data(100)
    inputs["inv_z"] = .5
    result = analyze_mechanism(inputs, cfg)
    assert result["status"] == "partial_exploration"
    assert result["main_test"]["p_value"] is None
    for output in result["bootstrap"].values():
        assert output["models"]["D2"]["invalid_repetitions"] == 24
        assert output["models"]["D2"]["slopes"]["Normal"]["ci_lower"] is None
    assert result["conclusion"]["category"] == "insufficient_evidence"


def test_config_hash_changes_with_seed_and_original_config_is_unchanged(cfg):
    original = deepcopy(cfg)
    first = analyze_mechanism(fixture_data(52), cfg)
    assert cfg == original
    cfg["bootstrap_seed"] += 1
    second = analyze_mechanism(fixture_data(52), cfg)
    assert first["protocol"]["config_sha256"] != second["protocol"]["config_sha256"]
