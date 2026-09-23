# Importing actual WTI observations

`engineering_replay.json` is a synthetic software test fixture. Its prices, dates and contract labels are invented. It is not evidence of a calibrated WTI market.

`parse_json(bytes)` or `load_json(path)` accepts one full `MarketSnapshot`, an ordered list of snapshots, or `{"snapshots": [...], "portfolio": [...], "settings": {...}}`. The fixture illustrates the schema. `parse_csv(bytes)` and `load_csv(path)` accept the same market data in flattened form. CSV does not carry positions or settings; those must be selected separately.

Every imported snapshot must include `contracts`, `quotes`, `as_of`, `mode`, `source`, `sequence`. Modes are `observed_eod`, `replay`, `live`, and `engineering_fixture`. Times need explicit UTC offsets. Replay sequence numbers must strictly increase and event times must not decrease. Snapshots represent complete states; absent quotes are explicit gaps, never filled from a previous file.

Every contract must include `contract_id`, `product`, `kind`, ordered `underlyings`, `expiry`, `multiplier`, `tick_size`, `currency`, `exercise`, `source`; options also need `strike` and `right`. Products are CL futures, LCE/LC European options, and B7A/7A European financial one-month calendar-spread options. Do not relabel LO American options, continuous futures or parent symbols. Exact underlying futures must be defined in the same snapshot. Price units are USD/bbl, multiplier is barrels/contract, strikes use the same price convention, and rates are annual decimals. Quotes need `contract_id`, `as_of`, `kind`, `source` plus observed prices. A settlement has `mark` and no invented bid/ask. `flags` contain data-quality issues; origin belongs in source/mode.

CSV requires all contract columns plus `snapshot_as_of`, `mode`, `snapshot_source`, `sequence`, `quote_as_of`, `quote_kind`, `quote_source`. Other columns: `rate`, `received_at`, `feed_alive`, `strike`, `right`, `bid`, `ask`, `bid_size`, `ask_size`, `mark`, `flags`. `underlyings` and `flags` cells contain JSON arrays. Shared snapshot fields must match on every row. One row contains one definition and its quote; an empty `quote_kind` is a quote gap. Repeat all definitions for each replay snapshot.

Source labels supplied by the importer are declarations, not independently verified truth. Retain vendor raw files and SHA-256 manifests privately in `options_lab_runs/private/`. Check `data_acquisition_status.json` for actual acquisition status. No data purchase, credentials or live feed is used automatically.

## Historical evidence protocol

`evaluate_history(observations, futures_only_trainer=..., futures_vanilla_trainer=...)` requires 120 distinct observed sessions. The final 40 chronological sessions are holdout; all preceding sessions train, with a minimum of 80. Trainers receive only the training observations. At holdout time each strategy receives a `DecisionView` without future marks. The unhedged, futures-only, and futures-plus-vanilla strategies share identical target exposure, observation IDs, marks and declared transaction-cost schedules. Quantities must be integer contracts. The evaluator calculates each gross P&L, charges entry and exit half-spread plus fees, then computes net P&L itself.

This is a paired one-session round-trip experiment, not a continuous self-financing backtest or proof of trading profitability. Entry and exit use the same declared half-spread schedule; a genuine execution study needs separate exit quotes and a continuous position ledger. Default trainers fit fixed-contract ridge hedge weights using training P&L only, then round and clip to integer lots. They reject changes to target exposure or hedge contract universe; they do not invent rollover mappings. Supplied trainers can implement other explicitly defined policies. A paired moving-block bootstrap reports 95% intervals for mean net P&L and P&L standard-deviation differences versus unhedged, with configurable block length (default 5), resamples (500) and fixed seed (20260923). Its stationarity/dependence assumptions are reported. Fewer than 120 observed sessions or test-fixture evidence returns `pending` with empty performance metrics. Raw source hashes are required; labels do not establish veracity by themselves.


## Optional live transport

No account is connected or required to inspect the application. The optional Databento transport reads only `DATABENTO_API_KEY` from the environment. Install its SDK separately only when setting up a licensed account. Before historical requests, call `DatabentoAdapter.estimate(request)` and obtain explicit authorization for `download(..., authorized=True, max_estimated_cost_usd=...)`. An estimate cap is not a guaranteed invoice ceiling; vendor account limits also matter. The code never purchases a subscription or agrees to licensing.

For an existing live entitlement, a separately supplied definition JSON must contain complete real `contracts`, `instrument_map` mapping numeric provider IDs to raw symbols, `portfolio`, and `settings`. Resolve the B7A spread's two actual CL legs before connecting. Then this explicit command captures a bounded stream privately and applies the same `DeskController` to each accepted complete state:

```sh
.venv-options/bin/python -m options_lab.adapters \
  --definitions options_lab_runs/private/resolved_definitions.json \
  --output options_lab_runs/private/live_capture_001 \
  --confirm-existing-entitlement --max-seconds 30 --max-records 1000
```

The capture writes snapshots, common-core result bundles, raw normalized event records, errors and SHA-256 hashes. It stops at the time/record limit or 20 MB of normalized snapshots. It preserves native vendor flag bits in the event log; exchange-specific flag semantics are not fully interpreted in this version. Live demonstration acceptance remains pending until actual account data, definitions, quote quality, stale/disconnect behavior and latency have been independently checked. The Streamlit UI can import the captured snapshots for replay; this command does not by itself establish a streaming browser session.

An eventual one-hour acceptance capture can be requested explicitly with `--max-seconds 3600 --max-records 2000000 --coalesce-seconds 5`. This keeps only the latest quote per instrument per interval, preserves its original event timestamp, and periodically stores/evaluates a full state. It is a sampled replay, not a tick-complete archive. Record and 20 MB limits still stop a capture; inspect actual elapsed time, data span and errors before claiming an hour of observation. Every startup or explicit `begin_resync()` clears the quote cache and keeps `feed_alive=False` until every required contract has a fresh post-reset record. Gateway errors require resynchronization; automatic reconnection is deliberately unimplemented. Live acceptance stays pending, including the actual disconnect/recovery exercise, until an authorized feed is available and tested. Historical SDK connect/read timeouts do not guarantee a total wall-clock limit for a long streaming HTTP response.
