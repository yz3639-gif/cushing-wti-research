# Exploratory mechanism extension — frozen specification

**Frozen at:** 2026-09-15 04:00:07 UTC, before fitting the new models.  
**Baseline already inspected:** `335912e0cd8c0f98`.  
**Status:** post-review exploratory extension. This is not a preregistration. The pooled regime summaries and annual correlations were already known; this historical sample is not an unseen test set.

The machine-readable specification is [mechanism.json](../config/mechanism.json). Any later change to these rules requires a separate, dated amendment and cannot silently replace this version. Numeric estimates and the final interpretation belong in generated results, not in this frozen document.

## Question, sample and information identity

Does a fixed two-knot relationship describe the historical Cushing inventory/WTI spread association beyond a straight line, and how dependent is that answer on calendar-year differences?

Use the existing public paired observations from 2015-01-01 through 2024-04-03, without extending prices beyond their available history. Every row must contain a finite seasonal inventory z-score and F2 minus F3 spread, a valid observation week and release date, and a quote date strictly before the release. Sort once by release date and assign an original release-cycle index. Exclude invalid records with explicit reasons; duplicate release dates are invalid inputs and stop the analysis. D1, D2 and the no-year-control variants use exactly the same valid rows.

The outcome is the **level** of the preceding EIA delivery-rank spread, in USD/barrel, and the regressor is inventory reported subsequently. Neither the pairing nor its fitted relationship measures what was tradable after the release. Actual monthly contract identity is unavailable. This analysis does not calculate returns, execution or added forecasting information.

The existing z-score uses the three preceding calendar years within plus/minus 28 circular seasonal days, at least 20 observations, and the sample standard deviation. Low is z < -1, High is z > 1; the two boundaries belong to Normal. These states are deviations from seasonal history, not capacity utilization or operational storage thresholds.

## Models fixed before the new estimates

For each record, use the observation week's month and day mapped onto leap year 2000, with a 366-day cycle, to construct `sin(2*pi*(day-1)/366)` and `cos(2*pi*(day-1)/366)`. Calendar-year indicators use the release year. Omit the earliest observed year's indicator to identify the intercept.

- **D1:** intercept + release-year indicators + seasonal sine/cosine + z.
- **D2:** D1 + `max(0, -1-z)` + `max(0, z-1)`.
- **D1_no_year_fe / D2_no_year_fe:** the same models with calendar-year indicators removed, retaining the intercept and seasonal controls.

Fit ordinary least squares. No knot search, alternative variable selection, profit-based selection or out-of-sample claim is permitted. D2 segment slopes are `beta_z - beta_low_hinge` for Low, `beta_z` for Normal, and `beta_z + beta_high_hinge` for High. Slopes have units USD/barrel per one z-score unit.

For visual comparison, conditional curves hold year and season controls at their common-sample column means. An optional adjusted scatter removes each record's D2 year/season contribution and restores its mean; label it explicitly as model-adjusted association. Raw scatter and conditional curves must not be silently overlaid as if they were the same quantity.

## HAC inference

The primary exploratory test is the D2 restriction that both hinge coefficients equal zero. Use the asymptotic chi-square Wald statistic with two restrictions, an OLS sandwich covariance, eight original release-cycle lags, Bartlett weights `1 - lag/(8+1)`, and the correction `n/(n-k)` for a full-rank design with k columns. All other coefficient and slope HAC intervals use the normal 97.5th percentile; report these as approximate exploratory intervals.

Write scores as `u_t = x_t * residual_t`. The uncorrected meat is `sum(u_t u_t') + sum_l w_l sum_t(u_t u_(t-l)' + u_(t-l) u_t')`; surround it with `(X'X)^(-1)` and apply the stated small-sample correction. Compute OLS and the inverse bread using the SVD of X to avoid squaring its condition number. Lag matching uses the **original** release-cycle indices, so a deleted year or excluded observation does not create a fictitious adjacency. The periods are publication cycles, not fixed calendar days.

The implementation uses the already locked NumPy and SciPy versions and is checked against an independent direct-loop sandwich calculation, exact coefficient fixtures and a chi-square reference. The covariance convention follows the [statsmodels HAC reference](https://www.statsmodels.org/stable/generated/statsmodels.stats.sandwich_covariance.cov_hac.html) and [robust covariance documentation](https://www.statsmodels.org/stable/generated/statsmodels.regression.linear_model.OLSResults.get_robustcov_results.html). No additional runtime dependency is required.

## Moving-block intervals

Use overlapping non-circular blocks of eight chronologically adjacent retained release records, sample block starts uniformly with replacement, concatenate and truncate to the original n rows. Draw 2,000 replicates with NumPy's `default_rng(4912816)`. Apply the same sampled row indices to D1, D2 and both no-year-control variants in each replicate. Refit every model, including the represented-year intercepts, in every replicate. Report percentile 95% intervals for each segment slope and D2's slope changes relative to Normal. Blocks of four and 13 repeat the identical procedure as sensitivity checks, using the same fixed seed independently for each block length.

The full-sample bootstrap is paired across models and retains the observed covariate/outcome pairs; it is not a residual bootstrap or an estimate of causal uncertainty. It does not preserve all multi-year structural changes. A replicate with no observations for a calendar year drops that unrepresented dummy and reports this occurrence. A rank-deficient, numerically ill-conditioned or unestimable replicate is recorded by reason and excluded from the corresponding model's interval; paired slope-difference intervals use only replicates in which both required models fit. The number of valid and invalid replicates is always shown. An interval with fewer than 95% valid requested replicates is reported as unavailable rather than presented as an unqualified 95% interval; the diagnostic percentile range remains separately labelled.

## Support and stability rules

Formal full-sample and leave-one-year-out inference on either tail requires at least 20 original observations in that tail across at least three release years. The joint hinge test requires both tails to pass. Point estimates remain visible when support is insufficient, with inferential p-values and intervals withheld. The support rule applies to the scope being analysed, not as a selection filter on each bootstrap replicate; tail representation failures within replicates are counted separately. Normal-state inference has no added arbitrary sample threshold but requires an identified fit.

For each calendar year, report n, date coverage, state counts, rank correlation, and the D1/D2 descriptive slopes where identifiable. Do not impose the three-year tail rule on these descriptive rows, and do not attach annual significance tests or confidence intervals. Flag 2024 as a partial calendar year.

Refit after deleting each year in turn, retaining the original time indices for HAC lag matching. Show the same-sample D1/D2 comparison, tail support, HAC slope intervals and joint Wald test; specifically label the exclusion of 2020. The full-sample bootstrap is not repeated for every deletion. Retain all deletion results, including unavailable and adverse results. This is sensitivity analysis, not a procedure for selecting the best historical subsample.

## Numerical failures and interpretation

Use relative SVD rank tolerance 1e-12, maximum condition number 1e10, positive residual degrees of freedom, and finite inputs. Reject a rank-deficient design rather than quietly choosing one of many possible hinge coefficients. If the two-restriction covariance lacks rank two, withhold the Wald test and report the cause. A zero-variance outcome has no defined R-squared. No value is made finite by replacing it with zero.

In-sample MAE/RMSE and R-squared summarize model fit only. A smaller D2 error is expected from additional terms and is not a forecasting improvement. Report the main joint test, slopes/intervals, no-year-control comparison, annual and deleted-year results together. A significant full-sample shape difference alone does not establish a stable relationship, a physical causal effect, or H2/H3. Confidence calculations address only some sampling dependence under this chosen exploratory workflow; they do not cover archive revisions or structural change.
