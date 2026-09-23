# Share the WTI Options Desk

**[Open the public demo](https://yz3639-gif.github.io/cushing-wti-research/options-desk/)**

The public walkthrough is a static HTML/CSS/JavaScript application hosted on
GitHub Pages, under `docs/options-desk/`. It requires no login, installation,
market-data account or connection to the developer's computer.
The page title and author credit are **WTI Options Desk | Antony Zuo** and
**Built by Antony Zuo**.

## What visitors can do

- Select one of three synthetic market snapshots.
- Shift CSO normal volatility by −1, −0.5, 0, +0.5 or +1 $/bbl/√year.
- Shift vanilla lognormal volatility by −5, 0 or +5 percentage points.
- Preview the selected case without changing active results, then Apply it.
- Inspect indicative quotes, whole-contract hedge tickets, costs and residual
  scenario risk from one consistent engine result bundle.
- Download the active case and inspect the numerical validation record.

These are exactly 45 stored Python-engine evaluations. The browser does not
interpolate results or run an online optimizer. Selecting another snapshot resets
both active and draft volatility to that snapshot's calibrated market case.
All inputs are explicitly synthetic; no live-feed or historical-performance
acceptance is implied. The proxy solutions are feasible bounded-search
incumbents, not proved optima. Raw solver diagnostics remain available.

## Source and numerical evidence

The initial data asset was produced by engine `0.2.0` at commit
`e45da888213389d1f2b9500d3905b78ee2cfd1c3`. The JSON keeps that provenance.

`docs/options-desk/verification.json` records 45 cases, 54,125 checks, no failed
checks, and three repeated evaluations that matched. The maximum recorded
reconciliation/repricing error is approximately $1.46e−11.

Recreate the asset using the isolated options environment:

```bash
.venv-options/bin/python -m options_lab.generate_public_demo
```

The default output is the ignored `options_lab_runs/public_demo_data/` folder.
Review its validation record before copying `demo-data.json` and
`verification.json` into `docs/options-desk/`. Solver versions and bounded-search
termination can affect incumbents; preserve source and diagnostic metadata.

GitHub Pages publishes `main:/docs`. The inventory report at the website root and
its research outputs are independent. Publishing the demo does not replace them.

## Full online Python application

The complete Streamlit application remains available locally and supports
arbitrary validated inputs. Its prepared public-cloud entrypoint is
`options_lab/cloud_app.py`; that separate hosting route is not deployed.
The static public walkthrough above is the link to share.
