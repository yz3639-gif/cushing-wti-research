# Historical validation of the actual desk policy

`desk_history.py` runs the same `options_lab.engine.evaluate()` used by the UI.
It is separate from the original fixed-contract ridge benchmark in
`research_validation.py`; the two reports must not be presented as the same study.

The input is a JSON object with an `episodes` array. Each episode contains:

- `decision`: `snapshot` (full MarketSnapshot), `portfolio` (one existing nonzero
  CSO position), `settings` (complete Settings), `known_at`, `source_sha256`.
- `exit_snapshot`: a full later MarketSnapshot with exact matching contract
  definitions for the initial position and selected hedges.
- `exit_source_sha256`: hash of the retained raw source used for the exit.
- `policy_id` and `policy_declared_at`: the fixed configuration and the timestamp
  when the target schedule was declared.

The loader constructs immutable input objects; `evaluate_episode()` constructs
its decision exclusively from the decision snapshot. Exit prices never enter
calibration or hedge selection. A JSON round-trip test verifies the same input and
result. The input schema can be generated without hand transcription with
`dataclasses.asdict(DeskEpisode(...))`.

```bash
.venv-options/bin/python -m options_lab.desk_history path/to/episodes.json \
  --output options_lab_runs/private/my-study/desk-history.json
```

The registered protocol requires at least 120 distinct decision dates. The last
40 form the holdout; all earlier episodes undergo the same data and feasibility
checks. Parameters and target schedules must be declared before the holdout and
remain fixed. This runner does not automatically select or fit parameters on the
development segment. It rejects inconsistent settings, overlapping episodes,
late-arriving decision inputs, missing or stale marks, negative option premiums,
infeasible strategies and expiry crossings without settlement rules. A failed
paired episode blocks aggregate performance metrics instead of silently dropping
the day. The raw source and policy declarations still need external verification.

Each episode starts with the same existing CSO inventory for all three comparisons:
unhedged, futures only, and futures plus vanilla options. Hedges are opened at the
decision and closed at the exit. Gross P&L uses observed mid changes; costs deduct
both observed entry and exit half-spreads and a fee on each side. Selected hedge
legs must have actual BBO observations at both endpoints. This is an explicit
crossing assumption, not a fill simulation or a guarantee of executable depth.
The model-to-market entry basis is not credited as a profit. Actual trade lots,
solver termination, time guard, costs and input version IDs are retained.

Target contract IDs can change between fresh episodes under the predeclared
schedule. Contract identity may never change within an episode. This supports an
explicit scheduled sequence of fresh exposures, not automatic rollover or a
continuous position ledger. Session counts currently use UTC decision dates;
exchange holidays, overnight sessions and the requirement for 120 valid trading
days require separate calendar acceptance.

`engineering_only` means the pipeline ran on synthetic fixtures.
`calculation_complete` means the input declared observed snapshots and passed
calculation checks; it does **not** authenticate those snapshots.
`real_data_acceptance` remains `pending_source_verification` until an independently
checked source manifest and trading calendar are available. No real 120-session
study has been completed in this workspace. No empirical hedge advantage is claimed.
