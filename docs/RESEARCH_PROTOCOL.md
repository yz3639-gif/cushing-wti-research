# Research protocol v1.0

**Implementation data exception, September 15, 2026:** publication-versus-current-series reconciliation identified EIA's lease-stock definition break at observation week October 7, 2016 (release October 13). The N1/N2 nationwide control remains implemented but is disabled pending verified harmonization. Cushing B0-B3 and their locked main comparison are unchanged. This is a data-acceptance restriction identified before any actual-contract performance result, not a response to favorable or unfavorable backtest results. Details and official source are in `TECHNICAL_APPENDIX.md`.

This protocol specifies the actual-contract study before any of its test results have been inspected. The fixed numeric decisions live in `config/research.json` and are hashed in the run manifest.

## Hypotheses

H1: inventory state and curve shape exhibit a nonlinear historical association. This is descriptive evidence.

H2: inventory features reduce test MAE relative to a market-only benchmark. This is the primary statistical test.

H3: the information improves cost-adjusted one-spread dollar P&L under predetermined execution rules. This is a separate economic test.

Public rank quotes support H1 exploration only. Actual-contract data is required for H2/H3. Failure to obtain that data is incomplete evidence, not a statistical rejection of H2/H3.

## Decisions and prices

Knowledge cutoff is 23:59:59 America/New_York on the actual release day. If source same-day settlement availability is unverified, quote features lag an additional complete session. Execution is next CME settlement; settlement plus fees/slippage is a simulation benchmark, not a guaranteed fill.

Select M2-M3 by verified actual expiry among unexpired identified CL monthly contracts. Hold the same pair to the next release execution, ten sessions, or five sessions before near expiry, whichever comes first. All price and target calculations use USD/barrel differences and permit negative prices.

## Models and time

B0 predicts zero. B1 contains current spread, five-session fixed-pair change, twenty-session change volatility, days to expiry and seasonal sine/cosine. B2 adds seasonal inventory z, one-week and four-week inventory changes. B3 adds low/high hinges at z=-1/+1 and z times one-week change. N1/N2 test incremental Cushing information conditional on other US commercial inventory.

The inventory reference uses prior three calendar years within circular 28-day seasonal distance, with at least twenty observations. It is not a tank-capacity threshold. Scalers are fit only on each training set.

Use Ridge alphas 0.1, 1, 10, 100; tune on 2019-2021 annual forward validation MAE, preferring stronger regularization for exact ties. Expanding annual fits start with 2015-2018. Include only complete labels ending before a five-session training gap. Main evaluation is 2022-2025. 2026 is a separate retrospective update. Exclude labels crossing train/validation/test/recent boundaries and log them; boundaries within the same partition retain complete labels.

## Inference and robustness

Primary result: paired B1 absolute error minus B3 absolute error on a common test sample. Report mean, 95% moving-block bootstrap interval, sample count and yearly differences. Main block length eight events; 2000 replications, fixed seed. Blocks of four and thirteen are sensitivity checks, not alternative headline selection.

Report B2/B3 and national controls, retune/retrain after removing development labels touching 2020, show 2023-2025 separately, evaluate M3-M4, and test one extra execution day. No profit-based hyperparameter or threshold optimization. The future-price block-bootstrap interval is conditional on this fixed protocol and does not establish causality.

## Trading accounting

Targets are -1/0/+1 spread using a fixed absolute prediction hurdle of 0.075 USD/barrel. Per-contract-side fee is $2.50. Baseline slippage is one tick; two/four ticks are cost stresses with signals frozen. Net actual contract positions before charging fees. Mark old positions first, then execute changes. Close all positions at evaluation end. Provide a forced-close/reopen turnover stress separately.

An absent entry input prevents entry. A missing mark during a prospective holding interval stops the full build. No trade is silently erased for having missing future data. Fewer than thirty active holding intervals is insufficient economic evidence. No capital return without a defined capital and margin model.

## Cases, claims and changes

Fixed event windows are March-June 2020 and July-November 2023. The failure case is the largest absolute B3 test forecast error with four release events on either side. Its selection is explanatory, not a model-selection input.

Results may be positive, negative, inconclusive, or unavailable. The report must retain unfavorable comparisons. Correcting a software defect requires new source hashes and a fresh run; changing the research question after viewing test results requires a new protocol version and explicit disclosure. No live order or automated trading is part of this project.
