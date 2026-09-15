# Release notes

## 1.0.0 — Public research edition

### Research coverage

- Publication-dated Cushing inventory reconstruction from official EIA archives, with original sources and local snapshot checksums.
- A matched public-data sample of 481 inventory releases from January 7, 2015 through April 3, 2024, paired with preceding EIA F2−F3 delivery-rank quotes.
- Seasonal inventory states, descriptive relationships and an exploratory comparison of linear and fixed-hinge specifications.
- HAC uncertainty, paired moving-block refits, year-deletion checks and source-linked cases for 2020, 2023 and 2019.

### Main finding

The pooled seasonal inventory/spread rank correlation is −0.729. With year and season controls, the joint test of the two fixed slope changes gives p = 0.775308. The main result does not establish additional nonlinear shape, and conclusions are sensitive to the treatment of variation across years.

The nonlinear extension was specified after inspection of the initial descriptive summaries and is identified throughout as exploratory. Historical association and in-sample fit are kept separate from predictive and economic evidence.

### Included artifacts

- Four-page English PDF and editable Markdown memo.
- Offline interactive report with event records, source links and sensitivity tables.
- Analysis notebook, English/Chinese interview notes and a three-minute portfolio guide.
- Machine-readable results, source audits, run manifest and documented input interfaces.
- Pinned Python dependencies and separate validation, build, report, test and reproduction commands.

### Research infrastructure

- Required-source validation checks both expected raw files and processed tables, so missing files cannot silently reduce the audited source set.
- The actual-contract workflow preserves contract identity across features, targets and holding intervals; incomplete validation periods and missing held-contract settlements block the relevant results.
- The ledger records contract-level daily holdings, settlement changes, position changes, costs and contributions, including reversals, rolls and terminal liquidation.
- Generated reports use one saved result set and distinguish development, main test and recent historical periods when verified market inputs are available.

### Current limitations

- The real-data edition is `public_evidence_only`. Verified actual-month prices, observed expiry dates and an energy settlement calendar remain required before forecasting or trading results can be accepted.
- Artificial fixtures verify software behavior and do not contribute empirical forecasts or P&L to this release.
- The nationwide inventory control remains disabled pending harmonization of EIA's October 2016 lease-stock definition change.
- Public inventory totals and seasonal scores cannot identify uncommitted tank space, operational capacity or available receiving arrangements.
- The matched public spread sample ends in April 2024. Later inventory observations do not extend that price history.

### Data distribution

This repository includes attributed public research inputs and outputs. Proprietary terminal exports are excluded. Any future use or redistribution of vendor data must comply with the applicable provider terms.
