# Market inputs not yet supplied

The current real-data run uses public EIA observations for description only.

To activate actual-contract evaluation, supply `contracts.csv`, `prices.csv`,
`sessions.csv` and `market_metadata.json` according to `docs/BLOOMBERG_EXPORT.md`.
This directory deliberately contains no invented or synthetic market rows.

Run `python -m cushing_research validate` to see the remaining requirements.
