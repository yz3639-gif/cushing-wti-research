# Technical appendix

## Reproduction and evidence states

`validate --scope public --offline` checks the complete public snapshot; missing actual-contract files remain an explicit diagnostic and do not fail that scope. `validate --scope full --offline` additionally requires accepted market inputs and is the default validation scope. A validation failure exits with code 2.

`build --offline` verifies required sources, reconstructs inventory features, runs the public mechanism module and writes one `outputs/results.json`. Actual-contract forecasting runs only after its independent prerequisites pass. `report --offline` rechecks source consistency and renders that saved result, without fitting another model or recalculating financial results in JavaScript. A changed code/configuration/input/dependency manifest requires rebuilding before rendering. The manifest includes input and code hashes, configuration, dependency versions and Python version; the generated run ID is authoritative and is not hardcoded here.

The delivered real-data edition has status `public_evidence_only`. Its association statistics are computed from official archived inventories and public delivery-rank quotes. There are no real actual-contract forecasts or trading results. Test fixtures are artificial inputs for software verification and are never used in the delivered research results.

A complete forecasting edition requires accepted actual contract data, a complete inventory source audit, every specified validation year, identical candidate validation samples, a common B0-B3 test sample and observations in every locked test year. Failed data gates produce explicit reasons. Missing marks within an otherwise eligible holding interval halt the run.

### Required source graph

`snapshot.verify_snapshot` derives the expected raw files from `inventory_audit.json.source_hashes`, `archive_index`, public-futures `sources` and cited supporting/cross-check records. Each referenced raw file, its `.source.json` sidecar, the audit SHA-256 and URL must agree. Every processed table requires an audit, matching row count and nonempty checksum. Inventory report IDs and row-level source links must match the manifest; public quote ranks must match their source list.

The snapshot includes the two original 2020 Table 4 files used to check mixed display precision, as explicit supporting sources. A cited latest-vintage cross-check also requires its raw sources and processed checksum. Thus deleting a raw file together with its sidecar, removing the processed checksum or deleting all raw files fails verification. Scanning additional present sidecars is supplementary, not the definition of completeness. These checks establish local consistency, not immutable publication history or cryptographic proof against simultaneous replacement of the complete data-and-audit package.

## Point-in-time inventory reconstruction

Each raw report has a publication date and a distinct observation week. The information boundary is 23:59:59 on the publication date in America/New_York, converted with daylight saving time. This deliberately accommodates both ordinary and late releases; it is not a model of the exact intraday release instant. Source tables must agree with the archive observation week before their current values are accepted.

For each observation week, the earliest archived publication is retained in the historical information set. Later revisions cannot replace it. Simultaneous publications enter together and create one decision, using the latest released observation week. A weekly change looks up the exact week seven days earlier; four-week change looks up exactly 28 days earlier. Neither uses forward filling.

For a seasonal anomaly, map month and day into the leap year 2000. Compute circular day distance on that 366-day axis, select the previous three calendar years within 28 days, and require at least 20 observations and positive sample standard deviation. This provides a deterministic treatment of year boundaries and February 29. It does not measure operational tank capacity.

## What the public-data analysis measures

### Nationwide stock definition break

The original US commercial-stock series includes lease stocks before the observation week ending October 7, 2016 and excludes them thereafter (first affected release October 13). EIA also backcast its current historical series. Consequently, a difference between a current download and an old report is not automatically a revision error. See [EIA's October 11, 2016 methodology explanation](https://www.eia.gov/todayinenergy/detail.php?id=28292).

Raw publication values are retained. National seasonal references and changes never span this definition boundary. The nationwide control is disabled by default pending verified historical harmonization; it is listed as an uncompleted auxiliary test. The main B0-B3 models require only Cushing and market features, so national missingness cannot select their sample. The code retains N1/N2 for a future accepted consistent input; synthetic tests exercise them without claiming real national-control evidence.

The descriptive series pairs each inventory release with the most recent public EIA F2-F3 quote strictly before that release date, up to seven calendar days earlier. Quotes are delivery ranks without persistent contract identities. The displayed sample starts in 2015 after inventory warmup and ends when the public quote series ends. It is not the primary 2022-2025 forecasting sample.

Rank correlation summarizes a monotone association. Regime medians and interquartile ranges describe distributions. Serial dependence, changing infrastructure, nationwide fundamentals and market anticipation can all contribute. No independent-observation significance test or causal interpretation is attached to those descriptive statistics. The separate D1/D2 extension below investigates shape; neither exercise establishes H2's incremental predictive information or H3's economic value.

## H1: the post-review exploratory extension

The methods and numerical failure rules were frozen in [MECHANISM_EXTENSION.md](MECHANISM_EXTENSION.md) and `config/mechanism.json` before the additional fits. Existing pooled/annual summaries had already been inspected. This is an explicitly exploratory extension, not a retrospectively claimed preregistration. It leaves the original [RESEARCH_PROTOCOL.md](RESEARCH_PROTOCOL.md) and B0-B3 rules intact.

The common sample has 481 releases from January 7, 2015 to April 3, 2024. D1 fits the preceding F2-F3 **level** on an intercept, inventory z, release-year intercept shifts and seasonal sine/cosine. D2 adds `max(0,-1-z)` and `max(0,z-1)`. Both use ordinary least squares; `D1_no_year_fe` and `D2_no_year_fe` retain seasonal terms but omit year indicators. Every model uses the same finite, date-valid rows. No knot search or profit-based selection occurs.

For D2, the Low slope is beta_z minus beta_low_hinge; Normal is beta_z; High is beta_z plus beta_high_hinge. A slope is USD/barrel per one z-score unit. Conditional display curves hold year/season columns at their common-sample means. The adjusted scatter removes each record's D2 year/season contribution and restores its mean; it is model-adjusted association, not a raw market quote or a forecast.

### Uncertainty and support

The main exploratory restriction is that both hinge coefficients are zero. Its chi-square Wald test has two restrictions and uses an eight-release-lag HAC covariance: Bartlett weights and the `n/(n-k)` correction. SVD determines identified fits. Deleted-year HAC calculations retain the original release-cycle indices, so removing a year does not turn distant observations into artificial neighbors.

Moving-block intervals resample pairs of observed rows in consecutive overlapping, non-circular eight-release blocks, refit each model and use 2,000 replications with seed 4912816. Blocks 4 and 13 are sensitivity checks. All model variants share sampled indices within a block setting. Fit failures, missing year categories and valid/invalid replication counts are retained. An interval with fewer than 95% valid replications is unavailable; a diagnostic percentile range does not silently become a valid interval.

Full-sample and deleted-year tail inference requires at least 20 observations in that tail across three release years. Both tails must qualify for the joint hinge test. Annual fits are descriptive only: identified point estimates remain, while annual p-values and intervals are withheld. 2024 is a partial year. Deleted-year checks are not a search for a preferred subsample; their full results remain available.

### What the fixed snapshot actually says

| Specification or deletion | Joint hinge p-value |
|---|---:|
| D2, year and season controls | 0.775308 |
| D2, seasonal controls but no year intercepts | 2.9009 × 10⁻⁷ |
| D2, leave out 2018, retain year/season controls | 1.3263 × 10⁻⁶ |
| D2, leave out 2020, retain year/season controls | 0.688234 |

The main eight-release D2 slope intervals are approximately Low [−0.4770, +0.0011], Normal [−0.6092, +0.0190] and High [−0.3980, +0.3911]. All cross zero. D2's Low-minus-Normal and High-minus-Normal slope-change intervals also cross zero. This does not prove the relationship is linear or absent. The no-year-control and excluded-2018 results show that shape conclusions depend on how historical variation is represented. Year controls change the comparison toward within-year variation; they do not establish a causal physical model.

The full JSON is `mechanism_results.json`, also embedded under `results.json.mechanism`. CSV exports retain the common sample, exclusions, model fit, coefficients, slope intervals, annual and deleted-year rows, and conditional curves. In-sample MAE/RMSE/R-squared describe fit only; a lower D2 fitting error is not predictive improvement.

## Historical cases and timing identities

Each public case includes release-level evidence with observation week, actual publication date, cutoff, inventory/z/changes, earlier quote date and direct source/hash. `case_studies.json` contains the interpretation, next operational check and potential falsifier; `case_<id>_evidence.csv` preserves its records.

- **2020:** fixed March-June window. The April 15 release and April 14 quote are the last paired record before April 20. The April 27 EIA explanation is identified as later context. A Normal seasonal state did not imply normal receiving-capacity risk. No development-period, hindsight-selected model is presented as an available 2020 forecast.
- **2023:** fixed July-November window. The first Low state is sequentially observable; the stock trough and spread peak are explicitly retrospective anchors. Verified actual-contract B1/B3 test records attach to this case only when available.
- **2019:** a post-review counterexample with within-year rank correlation about +0.079. It is weak association, not evidence of reliable mechanism reversal.
- **Maximum B3 error:** added mechanically in the full edition using the largest absolute error in the main test period; ties select the first chronological decision. The card distinguishes forecast error, position and realized simulated P&L. It does not replace the public 2019 counterexample or select model parameters.

## Actual-contract target construction

The importer requires a contiguous chain of actual delivery months, observed last trade dates, USD settlement marks, verified provenance and a supplied energy settlement calendar. Generic CL1/CL2 tickers fail validation. A calendar library is not treated as proof of actual historical settlement sessions.

At the information cutoff, select the second and third contracts with last trade dates later than the release date. Keep those exact identities for backward features, execution, exit and target calculation. Market features use only settlements already available by the cutoff, with an extra session lag unless same-day availability is verified. The expiry-distance feature is measured from the cutoff's latest session, regardless of price lag.

Entry is the next supplied settlement session. Exit is the earliest of the next release's execution session, ten holding sessions, or five sessions before the near contract's actual last trade date. Labels crossing a train/validation/test/recent partition boundary are excluded and counted. Uncompleted targets are pending. The outcome is the original contract pair's exit spread minus entry spread, in USD/barrel.

## Model fitting and uncertainty

Nonzero models fit an intercept and standardized features with the standard Ridge objective: summed squared residuals plus alpha times squared standardized slopes; the intercept is unpenalized. The scaler uses training data only. The alpha grid is 0.1, 1, 10, 100. Chronological annual predictions over 2019-2021 determine pooled validation MAE; ties prefer larger alpha. Validation outputs use development-selected penalties and are not independent final evidence.

Each required validation year must have eligible predictions. Every alpha for a model must cover exactly the same eligible event IDs; a missing year, dropped prediction or inconsistent candidate sample blocks that model's selection. The original pooled minimum of 26 remains; no additional annual minimum was invented. A partially represented year is displayed with its actual count rather than described as a complete year. `metadata.validation_coverage` records expected years, input/eligible counts, each candidate's annual counts, missing/duplicate IDs, sample hash, fitting failures and selected annual counts. `exclude_2020_validation_coverage` explicitly expects 2019 and 2021. Upstream source/feature/boundary exclusions are separately recorded by the pipeline.

Annual January 1 expanding fits use labels ending strictly before the five-session gap boundary. The 2022-2025 test years never select the feature specification or penalty. The 2026 fit only uses earlier completed labels and is a historical update, not a live record.

The main quantity is B1 MAE minus B3 MAE, evaluated on identical events. Positive is better for B3. The moving-block bootstrap samples consecutive blocks of eight events, truncates each resample to the original sample length and repeats 2,000 times with a fixed seed. Every model pair shares the same deterministic resampling design on its paired dates. The interval is conditional on this fitted research workflow; it does not quantify archive replacement, structural change or specification-selection uncertainty.

Prespecified sensitivity checks include 4/13-event blocks, excluding 2020 from development, 2023-2025 evaluation, B3 versus B2, other-region inventory controls and M3-M4. Delayed execution retains original forecasts and shifts execution only. It does not retune a new delayed strategy.

## Trade accounting

A positive spread forecast above 0.075 buys one near contract and sells one far contract. A negative forecast below -0.075 reverses the legs. Smaller forecasts imply no target position. Every leg represents 1,000 barrels.

Each daily mark earns the previous position times the settlement change times 1,000. Positions then change at the current settlement. Costs apply to absolute changes in each contract's net holdings, including two-contract changes on reversals or shared-leg rolls. Continuing an unchanged contract position has no fictional fill. Terminal positions are closed with costs. A second scenario closes and reopens every interval.

Per contract side, cost is $2.50 plus 1/2/4 ticks of $10. A standalone two-leg round trip therefore costs $50/$90/$170. Stress scenarios keep original signals. Negative settlement prices are valid; neither log returns nor price-denominator returns enter the ledger.

`contract_daily` retains each date/contract's starting position, previous/current settlement, held-position gross mark, fills, costs, ending position and net contribution. `contract_intervals` retains each decision/leg's position, gross P&L, allocated costs and net P&L. Closing and opening portions of net fills are attributed to outgoing and incoming events; unchanged carry creates no fee. At a forced close/reopen, total contracts traded can exceed the absolute net position change, so both measures remain explicit.

Daily leg rows reconcile to portfolio days; interval leg rows reconcile to event totals; each contract's daily and interval totals reconcile across the ledger. For example, long near/short far with price changes +$0.10/+$0.04 earns +$100/−$40, or +$60 gross. A roll from +A−B to +B−C trades −1 A, +2 B, −1 C; differences between contract price levels create no mark-to-market gain.

Drawdown is measured in dollars from a running peak initialized at zero. Cumulative negative daily gross settlement cash flows summarize variation cash outflows; they are not margin requirements or required capital. No capital return or Sharpe ratio is invented without a specified capital allocation. Baseline, cost pressure, forced close/reopen and extra-day execution scenarios retain distinct labels; delayed execution also stores original and simulated entry/exit dates.

## Module map

| Module | Responsibility |
|---|---|
| `data.py` | Archive discovery, cache, parse, source audits |
| `snapshot.py` | Required-source graph, raw/sidecar/processed consistency |
| `market.py` | Actual-contract import validation |
| `features.py` | Release-aware features, fixed-contract targets |
| `mechanism.py` | Exploratory D1/D2, HAC, block refits, annual and deleted-year checks |
| `cases.py` | Source-linked public cards and actual-contract failure attribution |
| `research.py` | Ridge, walk-forward validation, paired uncertainty |
| `ledger.py` | Contract-level daily positions, trades, costs and P&L |
| `pipeline.py` | Evidence gates, common result and manifest |
| `report.py` | HTML, four-page PDF, editable memo and interview notes |

See `RESEARCH_PROTOCOL.md` for the locked design, `DATA_DICTIONARY.md` for columns and `BLOOMBERG_EXPORT.md` for the remaining external input.
