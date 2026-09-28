# Fixed-ticket Risk Lab

The static Risk Lab adds three experiments to the saved synthetic desk cases:
cost sensitivity, omission of hedge legs at inception, and twelve additional
stress scenarios. It never re-optimizes the saved tickets. The extra scenarios
are fixed synthetic assumptions, not historical observations, probability
estimates, VaR, or market out-of-sample performance.

## Reproduce and verify

From the repository root, with the isolated options environment:

```bash
.venv-options/bin/python -B -m options_lab.risk_lab
.venv-options/bin/python -B -m options_lab.risk_lab --check
.venv-options/bin/python -B -m pytest tests_options/test_risk_lab.py -q
```

The exporter only writes `docs/options-desk/risk-lab-data.json`. Use `--output`
for another destination. It reads the original `demo-data.json`, the checked-in
engineering fixture, and saved active volatility nodes. It verifies the original
snapshot, portfolio, settings, active-volatility and bundle identities before
recomputing unit-contract P&L. The original demo data and verification record
remain unchanged. The exporter does not call a hedge optimizer.

## Sidecar schema 1.0

- `meta.base_demo_sha256` binds the exact original demo bytes. A browser must
  reject a mismatched demo/sidecar pair. Fixture and Python source hashes identify
  the inputs and exporter; the Git commit identifies the repository base, while
  source hashes also cover exporter changes not yet committed at generation.
- `scenario_sets.optimization` contains the original 100 named scenarios;
  `scenario_sets.challenge` contains 12 new, distinct, one-day scenarios. Every
  row has the full `Scenario` fields, including separate normal and lognormal
  volatility transformations.
- `cases` contains all 45 original preset keys and their bundle, snapshot,
  volatility, portfolio and settings IDs. Each case's
  `unit_pnl.{optimization,challenge}[contract_id]` is an ordered vector of
  one-contract dollar P&L from full repricing, before trading costs. All original
  contracts are included, not only the legs selected by a hedge.
- `validation` records full repricing, reconciliation errors, coverage and
  challenge risk-limit breaches. A breach is an experiment result and remains
  visible; it is not removed to make a hedge appear better.

The new challenge grid uses parallel moves of +/-12 USD/bbl, curve twists of
+/-5 USD/bbl, decoupled normal/lognormal volatility shocks, and their combinations.
The exact parameters are defined in `challenge_scenarios()` before evaluation.
No saved ticket is adjusted in response to the challenge results.

## Calculation contract

For scenario `s`, original portfolio `p`, retained hedge quantities `h`, and
unit-contract matrix `u`:

```text
base[s] = sum(p[j] * u[j][s])
gross[s] = base[s] + sum(h[j] * u[j][s])
cost = cost_scale * sum(original retained trade.total_cost)
net[s] = gross[s] - cost
worst loss = max(0, -min(net))
conservative risk constraint = max(abs(gross)) + cost <= risk_limit_dollars
```

Costs come from the original ticket's `total_cost`, `cost_per_lot` and
`cost_source`. They are checked independently against fixture BBO half-spreads,
contract multipliers and configured fees. Scaling costs does not change ticket
quantities or optimize a new proposal. Omission means the leg was never entered;
it is not a costless unwind of an executed position. Removing every hedge leg
returns the original unhedged portfolio with zero hedge-entry cost.

The original solver's feasibility/optimality status must not be attached to an
omission or cost-adjusted experiment. Recalculate position and gross-lot limits
as well as the conservative risk constraint. Challenge breaches do not imply the
original optimizer violated its own, different scenario grid.

## Acceptance

All 45 cases must reproduce the original unhedged and hedged 100-scenario P&L at
1x cost within $0.01. The exporter also checks every single-leg omission, complete
omission, independent per-leg fees, cost monotonicity, finite vectors, positive
shocked futures and option volatilities, unexpired contracts, all identities,
and a complete, unique challenge set. The tests include future P&L checked by
direct contract arithmetic and deliberate hash, identity, vector and shock
corruptions. All outputs are synthetic research experiments.
