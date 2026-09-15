# Implementation interfaces

All paths below are relative to the project root. Serialized dates are ISO strings. Numerical computation belongs to Python modules; reports read the common result. Synthetic fixtures are confined to tests/temporary QA and never populate the real research outputs.

## CLI and build identity

```text
python -m cushing_research validate --scope public --offline
python -m cushing_research validate --scope full --offline
python -m cushing_research build --offline
python -m cushing_research report --offline
python -m cushing_research fetch [--refresh]
```

Every command accepts `--root PATH`. `validate` defaults to full scope. Public validation requires the public snapshot and records missing actual-contract inputs diagnostically; full scope also requires accepted actual-contract provenance/data. Invalid or missing required inputs stop with exit code 2. `--offline --refresh` is invalid; fetching requires network and an explicit fetch command.

`pipeline.run_build(root: Path, config: dict) -> dict` writes the common result, audits, manifest and evidence CSVs. `report` verifies the required raw/processed snapshot again and requires a current source/configuration/input/dependency manifest: rebuild if its run ID differs from the stored result. The saved run ID is not a manually supplied parameter.

## Inventory ingestion and source gate

```python
fetch_inventory(root: Path, cutoff: str, refresh=False) -> (DataFrame, dict)
fetch_public_futures(root: Path, cutoff: str, refresh=False) -> (DataFrame, dict)
verify_snapshot(root: Path) -> dict
```

Ingestion functions are in `data.py`; strict verification is in `snapshot.py` and re-exported by import in `pipeline.py`.

- `inventory.csv`: `report_id`, `week_ending`, `release_date`, `knowledge_cutoff`, `cushing_mbbl`, `us_mbbl`, `source_url`, `sha256`, `timestamp_quality`.
- `public_futures.csv`: `trade_date`, F1–F4. Delivery ranks are descriptive only and stop April 5, 2024; they must never be fabricated into actual contract IDs.
- `inventory_audit.json`: source hashes, archive index, expected weeks, failures, replacements, supporting sources, processed checksum and optional latest-vintage comparison.
- `public_futures_audit.json`: per-rank sources, rows, failures and processed checksum.

`verify_snapshot` constructs required sources from those audits, checks raw/sidecar/hash/URL agreement, processed hashes and row/source links, and raises `ValueError` on failure. It returns `status`, `mode`, `expected_raw_files`, `verified_raw_files`, `verified_processed_files`, `processed_files`, inventory-link and public-rank counts, the rule and its limitation. Cited supporting/cross-check sources are required even if both raw file and sidecar were deleted. Actual-contract files are handled separately by market validation.

## User-supplied actual contracts

`market.validate_market(root, config) -> (frames: dict[str, DataFrame], audit: dict)`.

| File | Required content |
|---|---|
| `data/input/contracts.csv` | `contract_id`, delivery month YYYY-MM, actual `last_trade_date`, multiplier, tick size, exchange, currency |
| `data/input/prices.csv` | `trade_date`, actual `contract_id`, `settlement`; optional volume/open interest |
| `data/input/sessions.csv` | `trade_date`, timezone-bearing `settlement_time` |
| `data/input/market_metadata.json` | Source, settlement field, verified actual identities/expiries/calendar, availability rule, verification notes and verifier |

Provenance attestations must be explicit; missing fields are not silently true. Generic rank tickers, unsupported expiry information or missing held marks cannot produce an accepted complete backtest. See [BLOOMBERG_EXPORT.md](BLOOMBERG_EXPORT.md).

## Features and targets

```python
inventory_features(inventory: DataFrame, config: dict) -> DataFrame
public_observations(inventory: DataFrame, futures: DataFrame) -> DataFrame
build_events(inventory, contracts, prices, sessions, config,
             near_rank=2, execution_delay=0) -> (events: DataFrame, excluded: DataFrame)
```

`features.py` groups same-day publications into one information set and creates past-publication-only seasonal inventory features. Public observations match an earlier rank quote and are never trading labels.

Actual events contain `event_id`, decision/cutoff/week, entry/exit, actual near/far contract, `known_price_date`, fixed-pair spread/change/volatility, `dte`, seasonal terms, Cushing/national features, hinges/interaction, target `y`, partition and exit reason. `dte` is measured from the latest settlement session on/before the release cutoff, not from an additionally lagged price date.

`events` contains complete eligible targets; `excluded` contains source/feature/boundary exclusions and pending targets with reasons. Pair identities remain fixed within the label. Cross-partition labels are excluded; same-partition labels crossing a calendar year may remain. Extra execution delay changes simulated dates, not the information cutoff or selected pair.

## Public mechanism analysis

```python
analyze_mechanism(observations: DataFrame, config: dict | None = None) -> dict
```

The function is in `mechanism.py`; its configuration is `config/mechanism.json`, separate from the locked trading protocol. See [MECHANISM_EXTENSION.md](MECHANISM_EXTENSION.md) for its frozen post-review exploratory status and exact method.

Returned keys:

| Key | Meaning |
|---|---|
| `protocol`, `status`, `exclusions`, `sample` | Method/version/hash, evidence identity, common input/valid counts and tail support |
| `models` | D1/D2 and no-year-control fits: identification, coefficients, HAC covariance, segment slopes, joint tests and in-sample fit |
| `main_test` | Two-hinge restriction for D2 with year/season controls |
| `bootstrap` | Blocks 8/4/13, per-model refit failures/counts, slope and slope-change intervals, paired differences |
| `annual` | Per-year descriptive fits, counts/coverage and partial-year flag |
| `leave_one_year_out` | Every deleted-year refit, support and joint test, preserving original HAC time gaps |
| `curves`, `records` | Conditional display curves and common sample with fitted/adjusted values |
| `conclusion`, `curve_interpretation` | Evidence-based interpretation and display meaning |

Build exports `mechanism_results.json` and CSVs named `mechanism_sample`, `mechanism_exclusions`, `mechanism_coefficients`, `mechanism_model_fit`, `mechanism_slopes`, `mechanism_annual`, `mechanism_leave_one_year_out`, and `mechanism_curves`. JSON is authoritative for nested diagnostics. In-sample fits do not become out-of-sample performance.

## Actual-contract research

```python
run_research(events: DataFrame, sessions: DataFrame, config: dict) -> dict
```

`research.py` returns `predictions`, `metrics`, `comparisons`, `tuning`, `coefficients`, `robustness`, and `metadata`.

B0 is zero change; B1 has the six market inputs; B2 adds z and one/four-week inventory changes; B3 adds the two fixed hinges and z × one-week change. Explicitly enabled N1/N2 use a common complete national-feature subset without erasing primary events. The real nationwide control is disabled until its definition is harmonized.

Annual expanding Ridge fits use training-only scaling and completed labels before the five-session gap. Candidate alpha values are 0.1/1/10/100, selected on pooled chronological 2019–2021 MAE with stronger alpha winning ties. Every expected validation year and identical eligible candidate event samples are required. Metadata retains:

- `validation_coverage`: expected years, original/variant counts, candidate sample hashes, annual predicted counts, missing/duplicate IDs, fit failures and selected-year coverage.
- `exclude_2020_validation_coverage`: explicit required years 2019/2021 and variant status.
- `unavailable_models` and excluded-2020 unavailable models with reasons.
- Common test counts/mismatches, selected alphas and feature definitions.
- Fit cutoffs, gap boundaries, training counts and maximum training label end.

The primary B3-vs-B1 comparison uses paired test MAE loss differences with eight-event/2,000-replication block uncertainty. Development-selected predictions are labeled accordingly; 2026 is separate historical updating. The pipeline retains research eligibility metadata and upstream exclusions if a full result cannot be accepted.

## Ledger and attribution

```python
run_ledger(predictions: DataFrame, prices: DataFrame, sessions: DataFrame,
           config: dict, model='B3', cost_ticks=1,
           force_roundtrip=False, scenario='base') -> dict
```

`ledger.py` returns `daily`, `trades`, `intervals`, `contract_daily`, `contract_intervals`, and `summary`. Pass a single partition; the function filters the requested model. Forecast threshold ±0.075 gives spread quantity −1/0/+1, with near q and far −q.

Old held contracts earn daily variation margin before rebalancing at settlement. Normal continuation nets directly into the next target. Forced close/reopen records both fills; unchanged continuation under netting has no new costs. Caps, expiry protection and final exit end flat. Missing held-contract settlement raises an error; negative prices are valid.

Contract-day rows store starting/ending positions, previous/current settlement, net quantity change, absolute turnover, gross P&L, costs and net contribution. Contract-interval rows allocate those marks and opening/closing cost portions to each event/leg. Assertions reconcile leg days to portfolio days, leg intervals to intervals and whole-contract totals across both views.

The pipeline adds `partition`, `model`, `cost_ticks`, `force_roundtrip`, `scenario` to each ledger result and exports all five tables with scenario-aware names. Extra-day execution freezes the original forecasts and stores original/shifted execution dates. No capital return is calculated without an explicit capital base.

## Cases and report result

```python
build_cases(inventory, observations, research=None, ledgers=None) -> list[dict]
render_report(result: dict, output_dir: Path) -> dict
```

`cases.py` builds public 2020/2023/2019 cards from source-linked release evidence. Each card separates facts available then, retrospective anchors/explanations, interpretation, next operational check, falsifier and commercial impact. Verified actual test records attach to 2023; the full edition adds the mechanically chosen maximum B3-error card with position/P&L attribution. 2020 remains a development-era mechanism case.

`report.py` consumes the unified result:

- Identity: `schema_version`, title, author, generated time, cutoff, run ID, status.
- Answer: headline, conclusion, limitations, blockers and sources.
- Public evidence: inventory, public observations, descriptive summaries, mechanism and cases.
- Audits: inventory, futures, snapshot, market and research eligibility when attempted.
- Actual-contract evidence: research or null; scenario ledgers or an empty list.
- Configuration and coverage.

`public_evidence_only` contains real public evidence and explicit untested prediction/trading status. `full_research` additionally contains validated actual-contract comparisons and accounting. HTML/PDF/text/interview outputs read these same values. Plotly JavaScript is bundled for offline use; frontend filters change exploration views, not the formal full-sample conclusion.
