"""Synthetic fixtures exercise chronology; none are evidence about WTI markets."""
import json

import numpy as np
import pandas as pd
import pytest

from cushing_research.research import (
    MODEL_FEATURES,
    _fit_predict,
    _prepare,
    _training_rows,
    _walkforward,
    paired_mae_bootstrap,
    run_research,
)


def synthetic_fixture():
    """Explicitly invented observations: reusable for integration tests only."""
    rng = np.random.default_rng(421)
    dates = pd.date_range("2014-12-01", "2027-01-15", freq="B")
    sessions = pd.DataFrame({"trade_date": dates.strftime("%Y-%m-%d")})
    decisions = pd.date_range("2015-01-07", "2026-09-02", freq="W-WED")
    rows = []
    for i, day in enumerate(decisions):
        entry, exit_ = day + pd.Timedelta(days=1), day + pd.Timedelta(days=8)
        partition = lambda year: "train" if year <= 2018 else "validation" if year <= 2021 else "test" if year <= 2025 else "recent"
        if partition(day.year) != partition(exit_.year):
            continue
        values = rng.normal(size=9)
        z, delta1 = float(values[6]), float(values[7])
        row = {
            "event_id": f"SYNTHETIC-{i}", "decision_date": day.date().isoformat(),
            "cutoff": (day.tz_localize("America/New_York") + pd.Timedelta(hours=23, minutes=59)).isoformat(),
            "entry_date": entry.date().isoformat(), "exit_date": exit_.date().isoformat(),
            "near_contract": "SYNTHETIC-NEAR", "far_contract": "SYNTHETIC-FAR",
            "known_price_date": day.date().isoformat(),
            "spread": values[0], "delta_spread_5": values[1],
            "vol_spread_20": abs(values[2]), "dte": 30 + i % 22,
            "season_sin": np.sin(2 * np.pi * day.dayofyear / 365.25),
            "season_cos": np.cos(2 * np.pi * day.dayofyear / 365.25),
            "inv_z": z, "inv_delta1": delta1, "inv_delta4": values[8],
            "national_z": values[3], "national_delta1": values[4], "national_delta4": values[5],
            "low_hinge": max(0, -1-z), "high_hinge": max(0, z-1),
            "inv_interaction": z * delta1,
            "y": 0.3 * values[0] - 0.15 * values[1] + 0.12 * z * delta1 + rng.normal(scale=0.4),
            "partition": partition(day.year), "exit_reason": "next_report",
        }
        rows.append(row)
    config = {
        "train_start": "2015-01-01", "validation_years": [2019, 2020, 2021],
        "test_years": [2022, 2023, 2024, 2025], "recent_years": [2026],
        "alphas": [0.1, 1, 10, 100], "refit_gap_sessions": 5,
        "bootstrap_block": 8, "bootstrap_reps": 120, "bootstrap_seed": 91,
        "national_control_enabled": True,
    }
    return pd.DataFrame(rows), sessions, config


@pytest.fixture(scope="module")
def sample():
    return synthetic_fixture()


def synthetic_research_and_prices():
    """Add invented price paths reconciling each fixture label for ledger tests."""
    events, sessions, config = synthetic_fixture()
    calendar = pd.DatetimeIndex(sessions.trade_date)
    near = np.full(len(calendar), 70.0)
    level, previous = 70.0, 0
    for row in events.itertuples():
        entry = calendar.get_loc(row.entry_date)
        exit_ = calendar.get_loc(row.exit_date)
        near[previous:entry+1] = level
        near[entry:exit_+1] = np.linspace(level, level + row.y, exit_-entry+1)
        level += row.y
        previous = exit_
    near[previous:] = level
    prices = pd.DataFrame([
        {"trade_date": day, "contract_id": contract, "settlement": float(value)}
        for day, near_price in zip(sessions.trade_date, near)
        for contract, value in (("SYNTHETIC-NEAR", near_price), ("SYNTHETIC-FAR", 69.0))
    ])
    return events, prices, sessions, config


@pytest.fixture(scope="module")
def result(sample):
    return run_research(*sample)


def test_standard_ridge_matches_closed_form_and_uses_training_only_scaler():
    train = pd.DataFrame({"a": [-2., 0., 1., 7.], "b": [8., 2., -1., 3.], "y": [-5., 2., 3., 8.]})
    target = pd.DataFrame({"a": [10000.], "b": [-9000.]})
    prediction, fitted, scaler = _fit_predict(train, target, ("a", "b"), 2.5)
    x = (train[["a", "b"]].to_numpy() - train[["a", "b"]].mean().to_numpy()) / train[["a", "b"]].std(ddof=0).to_numpy()
    beta = np.linalg.solve(x.T @ x + 2.5 * np.eye(2), x.T @ (train.y - train.y.mean()))
    assert fitted.alpha == 2.5
    np.testing.assert_allclose(fitted.coef_, beta)
    np.testing.assert_allclose(scaler.mean_, train[["a", "b"]].mean())
    np.testing.assert_allclose(prediction, scaler.transform(target.to_numpy()) @ beta + train.y.mean())


def test_complete_schema_models_and_predeclared_comparisons(result):
    json.dumps(result, allow_nan=False)
    assert set(result) == {"predictions", "metrics", "comparisons", "tuning", "coefficients", "robustness", "metadata"}
    predictions = pd.DataFrame(result["predictions"])
    assert set(predictions.model) == set(MODEL_FEATURES)
    assert (predictions.loc[predictions.model == "B0", "prediction"] == 0).all()
    assert predictions.loc[predictions.partition == "validation", "evaluation_role"].eq("development_selected").all()
    assert set(result["metadata"]["selected_alphas"]) == set(MODEL_FEATURES)
    assert result["metadata"]["status"] == "complete"
    assert result["metadata"]["paired_test_events_complete"]
    assert len(set(result["metadata"]["test_model_event_counts"].values())) == 1
    primary = [row for row in result["comparisons"] if row["partition"] == "test" and row["primary"]]
    assert len(primary) == 1
    assert (primary[0]["candidate"], primary[0]["baseline"], primary[0]["block_size"]) == ("B3", "B1", 8)
    assert {row["check"] for row in result["robustness"]} >= {
        "bootstrap_block_4", "bootstrap_block_13", "national_inventory_control",
        "exclude_2020_retrain_retune", "test_2023_2025",
    }
    metric = next(row for row in result["metrics"] if row["partition"] == "test" and row["model"] == "B0" and row["scope"] == "overall")
    assert metric["direction_n"] == 0
    assert metric["direction_accuracy"] is None
    assert set(MODEL_FEATURES["B3"]) < set(MODEL_FEATURES["N2"])
    state_rows = [row for row in result["metrics"] if row["partition"] == "test" and row["model"] == "B3" and row["scope"] == "state"]
    assert {row["state"] for row in state_rows} == {"Low", "Normal", "High"}
    assert all(row["mae_improvement_fraction_vs_B1"] == pytest.approx(row["mae_improvement_vs_B1"] / row["B1_mae"]) for row in state_rows)
    coverage = result["metadata"]["validation_coverage"]
    assert coverage["status"] == "complete"
    assert coverage["expected_years"] == [2019, 2020, 2021]
    assert coverage["primary_common_candidate_sample"]
    assert all(coverage["same_sample_across_alphas"].values())
    assert {row["year"] for row in coverage["selected_year_counts"]["B3"]} == {2019, 2020, 2021}
    assert all(row["predicted_n"] == row["expected_eligible_n"] > 0 for row in coverage["selected_year_counts"]["B3"])


def test_yearly_gap_and_expanding_training_are_auditable(result):
    audits = [row for row in result["metadata"]["fit_audit"] if row["model"] == "B3"]
    assert all(row["max_training_label_end"] < row["gap_boundary"] for row in audits)
    assert all(pd.Timestamp(row["gap_boundary"]) < pd.Timestamp(row["refit_cutoff"]).tz_localize(None) for row in audits)
    test = [row for row in audits if 2022 <= row["prediction_year"] <= 2025]
    assert [row["training_n"] for row in test] == sorted({row["training_n"] for row in test})
    assert next(row for row in audits if row["prediction_year"] == 2023)["max_training_label_end"].startswith("2022")


def test_future_outcomes_and_features_cannot_change_earlier_predictions_or_tuning(sample, result):
    events, sessions, config = sample
    changed = events.copy()
    late = changed.decision_date >= "2023-01-01"
    changed.loc[late, "y"] = 1e7
    changed.loc[late, list(MODEL_FEATURES["N2"])] *= 1e4
    rerun = run_research(changed, sessions, config)
    assert rerun["metadata"]["selected_alphas"] == result["metadata"]["selected_alphas"]
    old = pd.DataFrame(result["predictions"])
    new = pd.DataFrame(rerun["predictions"])
    old = old.loc[old.decision_date < "2023-01-01"].sort_values(["event_id", "model"])
    new = new.loc[new.decision_date < "2023-01-01"].sort_values(["event_id", "model"])
    np.testing.assert_allclose(old.prediction, new.prediction, atol=0, rtol=0)


def test_gap_excluded_labels_do_not_enter_a_fit(sample):
    events, sessions, config = sample
    frame, dates = _prepare(events, sessions, config)
    training, boundary, _ = _training_rows(frame, dates, 2023, config)
    assert (training.exit_date < boundary).all()
    excluded = (frame.decision_date < "2023-01-01") & (frame.exit_date >= boundary)
    assert excluded.any(), "Fixture must contain a label purged at the refit boundary"
    original = _walkforward(frame, dates, "B3", 10, [2023], config)[0]
    frame.loc[excluded, "y"] = 1e12
    frame.loc[excluded, list(MODEL_FEATURES["N2"])] = 1e9
    changed = _walkforward(frame, dates, "B3", 10, [2023], config)[0]
    np.testing.assert_allclose([row["prediction"] for row in original], [row["prediction"] for row in changed], rtol=0, atol=0)


def test_recent_coefficients_are_frozen_and_recent_labels_never_train(sample):
    events, sessions, config = sample
    config = {**config, "recent_years": [2025, 2026]}
    frame, dates = _prepare(events, sessions, config)
    before, coefficients, audits = _walkforward(frame, dates, "B3", 10, [2025, 2026], config)
    frame.loc[frame._year >= 2025, "y"] = -1e10
    after, _, _ = _walkforward(frame, dates, "B3", 10, [2025, 2026], config)
    np.testing.assert_allclose([r["prediction"] for r in before], [r["prediction"] for r in after], rtol=0, atol=0)
    assert all(audit["fit_year"] == 2025 for audit in audits)
    slopes = pd.DataFrame(coefficients).pivot(index="feature", columns="prediction_year", values="coefficient")
    np.testing.assert_allclose(slopes[2025], slopes[2026], rtol=0, atol=0)


def test_ex2020_removes_affected_training_and_development_labels(sample):
    events, sessions, config = sample
    frame, dates = _prepare(events, sessions, config)
    training, _, _ = _training_rows(frame, dates, 2022, config, excluded_year=2020)
    assert not (training._year == 2020).any()
    assert not ((training.entry_date < "2021-01-01") & (training.exit_date >= "2020-01-01")).any()
    original = run_research(events, sessions, config)
    changed = events.copy()
    affected = ((changed.entry_date < "2021-01-01") & (changed.exit_date >= "2020-01-01")) | changed.decision_date.str.startswith("2020")
    changed.loc[affected, "y"] = 1e8
    changed.loc[affected, list(MODEL_FEATURES["N2"])] = -1e8
    rerun = run_research(changed, sessions, config)
    rows = lambda output: [row for row in output["robustness"] if row["check"] == "exclude_2020_retrain_retune"]
    assert rows(rerun) == rows(original)
    tuning = original["metadata"]["exclude_2020_tuning"]
    assert all(row["year"] != 2020 for row in tuning)


def test_mae_ties_prefer_stronger_penalty(sample):
    events, sessions, config = sample
    events = events.copy()
    events["y"] = 0.0
    output = run_research(events, sessions, config)
    assert all(alpha == 100 for model, alpha in output["metadata"]["selected_alphas"].items() if model != "B0")


def test_paired_bootstrap_preserves_pairing_and_is_reproducible():
    rows = []
    for i, day in enumerate(pd.date_range("2022-01-01", periods=110, freq="7D")):
        for model, prediction in (("B1", 0.0), ("B3", 0.8)):
            rows.append({"event_id": str(i), "decision_date": day.date().isoformat(), "model": model, "prediction": prediction, "y": 1.0})
    predictions = pd.DataFrame(rows)
    result = paired_mae_bootstrap(predictions)
    shuffled = paired_mae_bootstrap(predictions.sample(frac=1, random_state=8))
    assert result == shuffled
    assert result["n"] == 110
    assert result["mae_improvement"] == pytest.approx(0.8)
    assert result["ci_low"] == pytest.approx(0.8)
    assert result["ci_high"] == pytest.approx(0.8)
    assert result["evidence"] == "improvement"
    short = paired_mae_bootstrap(predictions.iloc[:20])
    assert short["n"] == 10 and short["exploratory"]
    assert short["ci_low"] is None and short["ci_high"] is None
    predictions.loc[0, "y"] = 100
    with pytest.raises(ValueError, match="identical observed targets"):
        paired_mae_bootstrap(predictions)


def test_insufficient_history_is_explicit_and_json_safe(sample):
    events, sessions, config = sample
    events = events.loc[events.decision_date >= "2022-01-01"]
    result = run_research(events, sessions, config)
    assert result["metadata"]["status"] == "incomplete"
    assert not result["metadata"]["paired_test_events_complete"]
    assert len(result["metadata"]["unavailable_models"]) == 5
    assert {row["model"] for row in result["predictions"]} == {"B0"}
    assert all(row["mae_improvement"] is None for row in result["comparisons"])
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("missing_years", [[2019], [2020], [2019, 2020]])
def test_missing_expected_validation_year_cannot_pass_on_pooled_count(sample, missing_years):
    events, sessions, config = sample
    events = events.loc[~pd.to_datetime(events.decision_date).dt.year.isin(missing_years)]
    output = run_research(events, sessions, {**config, "national_control_enabled": False})
    coverage = output["metadata"]["validation_coverage"]
    assert output["metadata"]["status"] == "incomplete"
    assert coverage["status"] == "incomplete"
    assert coverage["expected_years"] == [2019, 2020, 2021]
    assert {row["model"] for row in output["predictions"]} == {"B0"}
    assert output["metadata"]["selected_alphas"] == {"B0": None}
    for row in output["metadata"]["unavailable_models"]:
        assert row["reason"] == "incomplete_validation_year_coverage"
        assert row["missing_years"] == missing_years
    for candidate in coverage["candidate_audits"]:
        assert candidate["predicted_n"] >= 26, "The old pooled-count gate would wrongly accept this sample"
        assert candidate["missing_years"] == missing_years
    empty_years = [row for row in output["tuning"] if row["scope"] == "validation_year" and row["year"] in missing_years]
    assert len(empty_years) == 3 * 4 * len(missing_years)
    assert all(row["n"] == 0 and row["mae"] is None and not row["selected"] for row in empty_years)
    json.dumps(output, allow_nan=False)


def test_ex2020_has_explicit_legal_validation_years(sample):
    events, sessions, config = sample
    events = events.loc[~events.decision_date.str.startswith("2020")]
    output = run_research(events, sessions, {**config, "national_control_enabled": False})
    assert output["metadata"]["status"] == "incomplete", "The main protocol still requires 2020"
    excluded = output["metadata"]["exclude_2020_validation_coverage"]
    assert excluded["status"] == "complete"
    assert excluded["expected_years"] == [2019, 2021]
    assert excluded["variant_excluded_years"] == [2020]
    assert excluded["primary_common_candidate_sample"]
    assert not output["metadata"]["exclude_2020_unavailable_models"]
    assert all(row["year"] in {2019, 2021} for row in excluded["selected_year_counts"]["B3"])


def test_partial_validation_year_is_disclosed_without_inventing_a_new_threshold(sample):
    events, sessions, config = sample
    year_2019 = events.decision_date.str.startswith("2019")
    first_2019 = events.loc[year_2019].iloc[0].event_id
    events = events.loc[~year_2019 | events.event_id.eq(first_2019)]
    output = run_research(events, sessions, {**config, "national_control_enabled": False})
    assert output["metadata"]["status"] == "complete"
    coverage = output["metadata"]["validation_coverage"]
    year = next(row for row in coverage["selected_year_counts"]["B3"] if row["year"] == 2019)
    assert year["expected_eligible_n"] == year["predicted_n"] == 1
    assert coverage["minimum_pooled_observations"] == 26


def test_alpha_candidates_cannot_select_from_different_validation_events(sample, monkeypatch):
    import cushing_research.research as module
    original = module._walkforward

    def different_sample(frame, dates, model, alpha, years, config, excluded_year=None):
        result = original(frame, dates, model, alpha, years, config, excluded_year)
        if model == "B3" and alpha == 0.1 and excluded_year is None:
            return result[0][1:], result[1], result[2]
        return result

    monkeypatch.setattr(module, "_walkforward", different_sample)
    events, sessions, config = sample
    output = module.run_research(events, sessions, {**config, "national_control_enabled": False})
    assert output["metadata"]["status"] == "incomplete"
    assert not output["metadata"]["validation_coverage"]["same_sample_across_alphas"]["B3"]
    assert not output["metadata"]["validation_coverage"]["primary_common_candidate_sample"]
    assert "B3" not in output["metadata"]["selected_alphas"]
    unavailable = next(row for row in output["metadata"]["unavailable_models"] if row["model"] == "B3")
    assert unavailable["reason"] == "validation_candidate_sample_mismatch"


def test_failed_annual_fit_keeps_audit_and_blocks_tuning(sample):
    events, sessions, config = sample
    # Enough history for 2020 and 2021, but not the first required 2019 refit.
    output = run_research(events, sessions, {**config, "minimum_training_observations": 225, "national_control_enabled": False})
    assert output["metadata"]["status"] == "incomplete"
    b3 = next(row for row in output["metadata"]["validation_coverage"]["candidate_audits"] if row["model"] == "B3")
    year = next(row for row in b3["by_year"] if row["year"] == 2019)
    assert year["expected_eligible_n"] > 0
    assert year["predicted_n"] == 0
    assert next(row for row in b3["fit_audit"] if row["prediction_year"] == 2019)["status"] == "insufficient_training"


@pytest.mark.parametrize("national_values", ["missing_columns", "nonfinite", "valid_but_disabled"])
def test_national_control_defaults_pending_without_changing_primary_sample(sample, result, national_values):
    events, sessions, config = sample
    events = events.copy()
    config = {key: value for key, value in config.items() if key != "national_control_enabled"}
    national_columns = ["national_z", "national_delta1", "national_delta4"]
    if national_values == "missing_columns":
        events = events.drop(columns=national_columns)
    elif national_values == "nonfinite":
        events[national_columns] = np.nan
        events.loc[0, "national_z"] = np.inf
    output = run_research(events, sessions, config)
    assert output["metadata"]["status"] == "complete"
    assert output["metadata"]["paired_test_events_complete"]
    assert not output["metadata"]["national_control_enabled"]
    assert output["metadata"]["national_control"]["status"] == "pending"
    assert output["metadata"]["national_control"]["pending_reason"] == "Historical national stock definition break; lease-stock harmonization is not yet verified"
    assert output["metadata"]["enabled_models"] == ["B0", "B1", "B2", "B3"]
    assert {row["model"] for row in output["predictions"]} == {"B0", "B1", "B2", "B3"}
    assert not any(row.get("model", "").startswith("N") for key in ("metrics", "tuning", "coefficients", "robustness") for row in output[key])
    assert not any(row["candidate"].startswith("N") or row["baseline"].startswith("N") for row in output["comparisons"])
    old = pd.DataFrame(result["predictions"])
    old = old.loc[old.model.isin(["B0", "B1", "B2", "B3"])].sort_values(["event_id", "model"])
    new = pd.DataFrame(output["predictions"]).sort_values(["event_id", "model"])
    assert old.event_id.tolist() == new.event_id.tolist()
    np.testing.assert_allclose(old.prediction, new.prediction, atol=0, rtol=0)
    json.dumps(output, allow_nan=False)


def test_enabled_national_missing_data_remains_auxiliary(sample):
    events, sessions, config = sample
    events = events.drop(columns=["national_z", "national_delta1", "national_delta4"])
    output = run_research(events, sessions, config)
    assert output["metadata"]["status"] == "complete"
    assert output["metadata"]["national_control"]["status"] == "insufficient_history"
    assert {row["model"] for row in output["metadata"]["unavailable_models"]} == {"N1", "N2"}
    assert {row["model"] for row in output["predictions"]} == {"B0", "B1", "B2", "B3"}
    assert not any(row["candidate"].startswith("N") for row in output["comparisons"])


def test_enabled_national_models_share_complete_subset(sample):
    events, sessions, config = sample
    events = events.copy()
    events.loc[events.index % 4 == 0, "national_delta1"] = np.nan
    output = run_research(events, sessions, config)
    assert output["metadata"]["status"] == "complete"
    assert output["metadata"]["national_control"]["status"] == "complete"
    predictions = pd.DataFrame(output["predictions"])
    primary = set(predictions.loc[(predictions.partition == "test") & (predictions.model == "B3"), "event_id"])
    first = set(predictions.loc[(predictions.partition == "test") & (predictions.model == "N1"), "event_id"])
    second = set(predictions.loc[(predictions.partition == "test") & (predictions.model == "N2"), "event_id"])
    assert first == second
    assert first < primary


@pytest.mark.parametrize("problem", ["duplicate", "overlap", "naive_cutoff", "nonfinite", "non_session"])
def test_invalid_events_fail_closed(sample, problem):
    events, sessions, config = sample
    events = events.iloc[:20].copy()
    if problem == "duplicate":
        events.loc[1, "event_id"] = events.loc[0, "event_id"]
    elif problem == "overlap":
        events.loc[1, "entry_date"] = events.loc[0, "exit_date"][:8] + "14"
        events.loc[0, "exit_date"] = events.loc[2, "exit_date"]
    elif problem == "naive_cutoff":
        events.loc[0, "cutoff"] = "2015-01-07T23:59:00"
    elif problem == "nonfinite":
        events.loc[0, "spread"] = np.inf
    elif problem == "non_session":
        events.loc[0, "exit_date"] = "2015-01-17"
    with pytest.raises(ValueError):
        run_research(events, sessions, config)
