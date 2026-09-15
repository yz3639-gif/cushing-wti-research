# Data dictionary

Dates are ISO calendar strings unless explicitly described as timezone-bearing timestamps. Inventory is in **million barrels**; prices/spreads are **USD per barrel**; ledger amounts are **USD**. Missing values remain missing. A source audit, model fit or interval may be unavailable with a reason; missing values are never substitute zeros.

## 1. Publication data and release features

Raw accepted publication rows live in `data/processed/inventory.csv`. The derived `outputs/inventory_features.csv` has one decision per release date.

| Field | Definition |
|---|---|
| `report_id` | EIA archive issue identifier; distinct issues can share a release date |
| `week_ending` | Inventory observation week; not a trading information timestamp |
| `release_date` | Actual public release day from the archive |
| `knowledge_cutoff` | 23:59:59 on that day in New York, serialized with UTC offset |
| `cushing_mbbl` | Cushing crude oil stocks |
| `us_mbbl` | US commercial crude oil inventory excluding SPR; original pre-October-2016 scope includes lease stocks |
| `national_mbbl` | US commercial stocks less Cushing; an auxiliary regional control, not the Cushing total |
| `inv_z` | Inventory minus previous-three-calendar-year same-season mean, divided by sample standard deviation |
| `inv_delta1`, `inv_delta4` | Difference from exactly one/four observation weeks earlier in the known publication record |
| `inv_reference_n`, `inv_reference_mean`, `inv_reference_std` | Seasonal reference count, mean and sample standard deviation; mean/std are million barrels |
| `state` | `Low`: z < −1; `Normal`: −1 ≤ z ≤ +1; `High`: z > +1; `Unavailable` when no valid z |
| `reports_on_release_date` | Number of original issues entering that release's information set |
| `source_url`, `sha256` | Accepted original source URL and its SHA-256 |
| `timestamp_quality` | End-of-release-day convention; does not claim an exact intraday publication timestamp |

The seasonal reference uses month/day mapped to leap year 2000, circular distance within 28 days, at least 20 observations and positive sample standard deviation. States describe deviations from historical seasonality, not working-capacity utilization or operational minimum stocks.

The earliest available publication for each observation week is retained. All reports released on one date enter together; the latest observation week defines the current state. Subsequent revisions do not overwrite earlier decisions. National features do not cross the documented lease-stock definition boundary; the actual national-control analysis remains disabled pending harmonization.

## 2. Source and eligibility audits

| Location | Meaning |
|---|---|
| `inventory_audit.json.source_hashes` | Accepted source per original report, including `report_id`, `raw_file`, `url`, `sha256` |
| `archive_index` | Cached index URL/hash; its standard raw location is `data/raw/eia_archive_index.html` |
| `supporting_sources` | Required evidence files used in source reconciliation, including the two 2020 Table 4 checks |
| `source_fallbacks` | Rejected source and accepted same-release replacement, with reason and retained precision |
| `latest_vintage_crosscheck` | Diagnostic comparison against later history; referenced sources and processed output are also verified |
| `public_futures_audit.json.sources` | Public source file/hash/URL for each delivery rank |
| `processed_sha256` | Mandatory checksum of a processed CSV; absence is a validation failure |
| `data_audit.snapshot` | Expected/verified raw counts, processed checks, source-link counts and verification rule |
| `data_audit.research_eligibility` | Actual-contract model eligibility metadata when that stage has been attempted |

Raw bytes, sidecars, source manifests and processed links must agree. These hashes prove local snapshot consistency, not unchanged original-publication history.

## 3. Public observations

`outputs/public_descriptive_observations.csv` contains `decision_date`, `week_ending`, `cushing_mbbl`, `inv_z`, `state`, `price_date`, `F2`, `F3`, `spread`, `price_identity` and `relationship`.

F1–F4 in the underlying source table are EIA **delivery-rank observations**, not contract IDs. `spread = F2 − F3`; positive is backwardation for that curve segment. `price_date` is strictly earlier than `decision_date` and at most seven calendar days old. Quotes stop on April 5, 2024 and are never extended across later inventory reports. The current matched research sample ends April 3, 2024.

`spearman` describes rank association. Regime `q25`/`q75` values are the observed spread distribution's quartiles, not confidence intervals. Public rank changes cannot establish fixed-contract P&L or post-release forecasting value.

## 4. Exploratory mechanism outputs

`outputs/mechanism_results.json`, also stored under `results.json.mechanism`, is the authoritative nested result. Its `protocol` contains the frozen extension version, configuration hash, fixed knots, HAC/bootstrap rules and explicit post-review evidence label. The original prediction protocol is separate.

| File or result key | Contents |
|---|---|
| `mechanism_sample.csv` / `records` | Common sample, observed spread, z, original `period_index`, season terms and D1/D2 fitted levels |
| `mechanism_exclusions.csv` / `exclusions` | Invalid public observations and exclusion reasons; can be empty |
| `mechanism_model_fit.csv` / `models` | Fit status, n, year-control flag, in-sample MAE/RMSE/R-squared and joint hinge p-value |
| `mechanism_coefficients.csv` | `model`, `term`, estimate, HAC standard error/interval and inference status |
| `mechanism_slopes.csv` | `block_length`, `model`, `state`, point slope, interval status, valid/invalid refits and percentile limits |
| `mechanism_annual.csv` / `annual` | Year, n, dates, `partial_year`, rank correlation, state counts and nested descriptive model fits |
| `mechanism_leave_one_year_out.csv` | Excluded year, n, test status/p-value, D2 segment slopes and HAC gap policy |
| `mechanism_curves.csv` / `curves` | z grid with D1/D2 conditional fitted levels, including no-year-control variants |
| `main_test` | D2 joint restriction `low_hinge = high_hinge = 0`, chi-square statistic/df, HAC convention, p-value and status |
| `bootstrap` | All block lengths, per-model refit failures, slope/slope-change intervals and paired model differences |
| `sample.tail_support` | Tail observation count, represented years, inference eligibility and reason |

Nested dictionaries in diagnostic CSV columns are for inspection; use the JSON structure for programmatic access rather than evaluating CSV strings as Python code.

- **D1:** intercept + year indicators + seasonal sine/cosine + z.
- **D2:** D1 + low/high hinges at fixed z = −1/+1.
- **`*_no_year_fe`:** removes year indicators, retaining seasonality.
- **`fitted_D1`, `fitted_D2`:** in-sample historical spread-level fits; not forecasts.
- **`adjusted_spread`:** observed spread minus each record's D2 year/season contribution plus the common-sample mean contribution.
- **`period_index`:** original sorted release-cycle index retained for HAC lag distances after exclusions/deleting years.
- **Slope units:** USD/barrel per one z unit. Low is beta_z − beta_low_hinge; Normal beta_z; High beta_z + beta_high_hinge.
- **`ci_lower`, `ci_upper`:** reported bootstrap limits only when inferential support/refit rules pass. `diagnostic_percentile_*` can remain when qualified inference is unavailable.
- **`hac_ci_lower`, `hac_ci_upper`:** approximate HAC normal intervals, a different method from the moving-block intervals.
- **Annual inference:** identified point slopes are descriptive; annual p-values and intervals are withheld. A missing tail can make annual D2 unidentified.

All deleted-year checks, including exclusion of 2018 and 2020, remain visible. A smaller in-sample fitting error or a significant hinge test is not a forecasting improvement or causal effect.

## 5. Case evidence

`case_studies.json` stores each case's `selection`, `evidence`, `anchors`, `known_then`, `interpretation`, `next_check`, `falsifier`, `commercial_impact`, `model_judgment` and `subsequently_observed` where applicable. `case_<id>_evidence.csv` exports the release-level evidence.

Evidence records retain inventory/feature fields, matched `price_date`, F2/F3/spread, source URL/hash, `quote_source_url`, `evidence_id` and `quote_identity`. `anchors.anchor_label` identifies sequential observations versus retrospective extrema. A `context_sources.published_date` later than the decision is explanatory context, not an eligible historical input.

The public cards use IDs `2020`, `2023` and `2019`. A full actual-contract edition adds `failure`, selected by maximum absolute B3 test error. `actual_predictions`, `actual_intervals` and `contract_contributions` connect that failure and the 2023 case to stored actual-contract outputs. Maximum forecast error need not produce a position or be the largest trade loss.

## 6. Actual-contract events and model coverage

`near_contract` and `far_contract` stay fixed for the target. `known_price_date` is the last settlement permitted by the provider availability rule. `entry_date` follows the cutoff; `exit_date` is the earliest of the next execution, ten held sessions or the five-session pre-expiry boundary. Excluded/pending records preserve their reasons in `excluded_events.csv`.

`spread`, `delta_spread_5` and `vol_spread_20` use that fixed pair. **`dte` counts verified settlement sessions from the latest session on/before the release cutoff to the near contract's last session; it is not measured from an additionally lagged `known_price_date`.** Seasonality uses the observation week's 366-day circular month/day map. `y = exit_spread − entry_spread`, USD/barrel. No price-denominator return is used.

Predictions add `model`, `prediction`, `alpha`, `fit_year`, `training_n` and `evaluation_role`. `development_selected` identifies validation-period output using parameters chosen on development data; it is not independent final evidence.

`research.metadata.validation_coverage` records:

| Field | Definition |
|---|---|
| `expected_years`, `variant_excluded_years` | Required development years and explicit variant exclusions |
| `input_year_counts` | Counts reaching research before/after variant exclusion; upstream exclusions are recorded separately |
| `candidate_audits` | Per-model/alpha expected and actual sample, missing/duplicate IDs, actual prediction years, annual counts and fitting audits |
| `sample_sha256` | Deterministic hash of the candidate event-ID sample, including multiplicity |
| `same_sample_across_alphas` | Whether all candidate penalties for a model used the same sample |
| `primary_common_candidate_sample` | Whether the primary nonzero models' candidate validation samples agree |
| `selected_year_counts` | Actual per-year coverage of each selected model |
| `minimum_pooled_observations` | Original pooled count threshold, 26 by default; no separate annual minimum |

The main expected years are 2019/2020/2021. `exclude_2020_validation_coverage` explicitly expects 2019/2021. Missing a required year or changing a candidate's effective sample blocks selection. `paired_test_events_complete` and per-model missing IDs verify B0–B3 test comparability.

## 7. Dollar ledger and attribution

Every ledger is identified by `partition`, `model`, `cost_ticks`, `force_roundtrip` and `scenario`. Scenario-aware CSV names follow `<table>_<partition>_<model>_<ticks>ticks_<scenario>.csv`. `base`, `forced_roundtrip` and `extra_execution_day` remain distinct; cost scenarios preserve the original signals. Extra-day ledgers store original and simulated entry/exit dates under `execution_dates`.

| Table | Principal fields and meaning |
|---|---|
| `daily` | Date, portfolio `gross_pnl`, `fees`, `slippage`, `net_pnl`, cumulative P&L, drawdown, contracts traded and ending position dictionary |
| `trades` | Actual contract, signed quantity change, contracts traded, old/new quantities, closing/opening quantities, settlement, costs, phase and incoming/outgoing event IDs |
| `intervals` | Event ID, fixed pair, entry/exit, forecast, signed spread `quantity`, observed y, gross/cost/net P&L |
| `contract_daily` | Date/contract/scenario, `start_position`, `previous_settlement`, `current_settlement`, gross mark, `quantity_change`, `contracts_traded`, costs, `end_position`, `net_contribution`, start/end event IDs |
| `contract_intervals` | Event/contract/leg/scenario, signed leg `quantity`, gross mark, costs, `net_pnl` and contracts traded attributed to the interval |

`gross_pnl` equals the old position × settlement change × 1,000 barrels. Fees/slippage are positive costs; `net_pnl = gross_pnl − fees − slippage`. An entry has no prior held mark: `previous_settlement` is missing when the start position is zero, rather than an invented zero price.

`quantity_change` is signed net position change. `contracts_traded` counts absolute executed quantities. They coincide in magnitude for one net rebalance; forced close/reopen can have positive turnover and zero net change. Reversals and common-contract rolls may require two contracts on a leg. Unchanged continuation has no new fill.

Contract days reconcile to portfolio days, contract intervals to event totals, and each contract's full daily and interval totals to one another. Drawdown, worst-day/interval P&L, break-even cost and variation cash outflow are dollar/cost measures. They are not account returns, required capital or broker margin requirements.
