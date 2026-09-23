# WTI Options Desk Lab

A local Streamlit desk for a WTI calendar-spread option, editable volatility inputs, proxy hedges, and scenario risk. This is a separate application beside the Cushing inventory research package. It does not alter that research or route orders.

## Start locally

From the repository root:

```bash
python3.12 -m venv .venv-options
.venv-options/bin/python -m pip install -r options_lab/requirements.lock.txt
.venv-options/bin/python -m streamlit run options_lab/app.py --server.address 127.0.0.1
```

On macOS, double-click `run_options_desk.command` after installation, or run it from the repository root. It binds only to `127.0.0.1` on port 8501.

The default screen contains no market prices. Load a local snapshot that you are authorized to use. To inspect the engineering workflow without market data, explicitly select **Engineering fixture – not market data** and load it. The fixture label stays visible throughout that session.

## Working with a snapshot

1. Review the data source, observation time, contract months, and stale-data status.
2. Open the volatility manager. **Market** is the imported reference, **Draft** holds proposed edits, and **Active** is used for valuation.
3. Edit draft nodes or apply a shift. Inspect normal and lognormal volatility separately: their units differ.
4. Preview the change. Compact tables compare value, both quote sides and sizes, each hedge contract, trade counts, and cost. The explanation exposes inventory, stress penalties, and crossing-cost assumptions. Pricing, quotes, integer hedge positions, and stress belong to a single versioned result bundle.
5. Apply a valid preview. An invalid draft leaves the previous active result intact and displays the reason. A failed calculation on a new market clears old prices, quotes, hedges, and the preview, because they no longer describe the current market.
6. For a sequence of snapshots, use Step or Play/Pause. A failed or rejected record is consumed once, recorded in the failure log, and pauses playback. The next Step or Play continues with the next observation. Playback follows local observations; it is not a live connection.

The Draft panel also accepts a `VolVersion` JSON object, a JSON array of `VolNode` records, or a CSV with `VolNode` field names. In CSV, encode `underlyings` as a JSON array. Imported `value` uses `decimal_annual` for lognormal nodes (for example, `0.32` means 32%) and `usd_per_bbl_sqrt_year` for normal nodes. Displayed lognormal values are annualized percentages; parallel shifts use percentage points. Normal and lognormal tables are separated in Market, Draft, and Active. Preview checks the imported draft before it can be applied.

## Reading the outputs

- A **model value** depends on the model and its active inputs.
- A **modeled quote** includes explicit quote assumptions. It is not a broker or exchange bid/ask.
- **Integer proxy hedges** use whole contracts. Residual exposure and any unavailable hedge instruments matter alongside the headline value.
- **Stress** is a hypothetical revaluation, not a historical return or realized P&L.
- **Stale/error** states must remain visible. A last successful bundle is not a fresh calculation simply because it remains on screen.
- **Versions** identify the snapshot, volatility state, and valuation bundle used together.

## Save and restore a full session

Open **Save / restore full session** in the sidebar and download the session JSON. Unlike the result-bundle download, it contains the actual contracts and observations, Market/Draft/Active nodes, historical volatility versions and their validation contexts, frozen draft and preview inputs, positions, settings, the replay queue and consumed-record cursor, error log, source provenance, and source-file hash. Keep it private when any underlying input is licensed or confidential.

To resume after restarting the app, select that JSON in **Saved desk session** and click **Restore full session**. Import builds a separate temporary desk, checks the content SHA-256, schema, contract and version relationships, recalibrates Market, and recomputes the saved active result and preview before replacing the current session. Inconsistent or non-reproducing files leave the existing desk untouched. An invalid staged Draft can be retained with its validation errors; it cannot become Active through import. A saved failed market calculation remains empty of advice and can advance to a later good replay record.

Playback always resumes paused. A session file never opens a provider connection, imports credentials, or routes orders. The SHA-256 detects changes to saved content; it does not authenticate a data vendor or verify a preserved source-file hash without the original source file. The import requires the same engine schema and exact reproduced result. Changed engine behavior, dependencies, or a solver time guard may cause rejection instead of silently replacing the saved result with different output.

The Python API is `options_lab.session_io.export_session(...)` / `import_session(...)`. Recovery tests include a fresh Python process, failed-record recovery, frozen manual drafts, history preservation, tampering, and transactional UI installation:

```bash
.venv-options/bin/python -m pytest tests_options/test_session_recovery.py -q
```

## Data and connection boundary

Use identified futures months and option products. Do not substitute a continuous futures series for the two contract legs, or American WTI options for European options without an explicit model change. Settlement observations must remain labeled as settlements; they do not establish an executable spread or an intraday tape.

The live connection panel reports pending/unconfigured status until an authorized adapter is available. The app neither creates an account nor purchases a feed. Public visibility of data does not establish the right to import, redistribute, or use it for software development. Keep locally supplied data outside public releases unless its license permits redistribution.

The bundled engineering fixture is synthetic and exists only for mechanics and tests. It provides no evidence about market calibration, executable prices, hedging performance, or profitability.

See [the one-page brief](brief.md) and [the three-minute demo script](demo_script.md) for a concise walkthrough.

## Reproduce the validation

```bash
.venv-options/bin/python -m options_lab.validate --updates 1000
```

Results go to the ignored local directory `options_lab_runs/validation/`; use `--output` to select another directory. See the public [validation summary](VALIDATION.md) for the recorded 0.2.0 results and the [optional CI template](ci/options-desk.yml.example) for separate Linux checks. This template is not yet enabled. The repair run processed **1,000 distinct changing engineering states**, 20 contracts and 100 scenarios per update, at **0.122 seconds p95**, with zero failed updates or quote/position/version violations. Original research preservation covered 1,661 files with zero changes. A checked-in manifest of those already-public files lets fresh clones run the same preservation gate; local pre-change baselines take precedence when present.

Timing covers deserialization through calibration, checks, full scenario evaluation, integer optimizers and JSON export, with independent output constraints checked in the same loop. Synthetic input generation and browser transport/paint are excluded; browser paint has not been benchmarked 1,000 times. Prices, normal/lognormal volatility and timestamps vary between engineering states. Timings are machine-specific. All proxy results in this benchmark reached the configured solver limit: they were checked feasible solutions, not proved optima. The CLI exits unsuccessfully if any engineering hard gate fails or is incomplete, including skipped tests, insufficient update counts, latency, invalid quotes, prohibited instruments, risk/position constraints, mixed versions, P&L reconciliation or original-file changes.

All original research files in the pre-change preservation manifest remained unchanged. Real-data accuracy, a 120-session study and a 60-minute live run remain pending. See [the data acquisition path](DATA_NEXT_STEPS.md) and [input formats and live capture](examples/import_format.md).

## Historical validation of the desk policy

The new [desk-policy historical runner](DESK_HISTORY.md) calls the same `evaluate()` as the UI and keeps the original ridge benchmark separate. It validates every development and holdout episode, uses the last 40 for comparison, charges observed entry/exit spreads and fees, and rejects unavailable paired sessions rather than silently excluding them. Synthetic inputs remain `engineering_only`. Exchange-calendar/source verification and a real 120-trading-day study are still pending.

## Generated deliverables

The one-page PDF, three-minute English video and video transcript are in `options_lab_runs/deliverables/`. The video is an edited, narrated walkthrough using actual application screenshots; it is not a continuous screen recording. It uses synthetic inputs. Generators are `python -m options_lab.build_brief` and, on macOS with `say`, `python -m options_lab.build_demo` after actual app screenshots have been captured in `options_lab_runs/demo/`.

Keep `options_lab_runs/` private and outside any release package. It can contain user-supplied licensed raw files. The original project's release packager is unchanged and does not include this directory.
