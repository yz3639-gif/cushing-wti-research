# WTI Options Desk Lab

**A local, reviewable workflow for a calendar-spread option, its proxy hedges, and its risk.**

A calendar-spread option depends on two futures months. A change in the forward curve, either vanilla volatility, or their dependence can alter both its value and its hedge. This lab makes those inputs and their versions visible together so that a user can inspect a proposed volatility change before activating it.

The workflow begins with an authorized local market snapshot containing identified contracts and provenance. Market, Draft, and Active volatility states remain distinct. Users edit a draft, preview the effect on the same valuation bundle, and apply only a valid result. Normal volatility is shown in dollars per barrel per square-root year; lognormal volatility is shown in percent per square-root year. They are never treated as interchangeable.

The desk combines model value, an explicitly parameterized quote, integer proxy hedges, and scenario risk. Contract counts, residual exposures, model assumptions, timestamps, stale-data status, and bundle versions are part of the output. A hedge is an approximation subject to contract granularity and scenario/model risk. A modeled quote is not an executable market quote.

**Three-minute demonstration:** load an authorized snapshot; inspect the underlying futures and volatility nodes; change one draft node or shift; preview value and hedge changes; apply the new version; step through a local replay and inspect the resulting risk bundle.

The app starts without market data. A separate, explicitly selected engineering fixture demonstrates mechanics and is labeled **“Engineering fixture – not market data.”** It is not evidence of pricing accuracy, a real trade, historical performance, or live connectivity. Daily settlements support snapshot analysis; they do not establish a bid–ask spread, execution quality, or intraday hedging performance.

**Current scope:** local files, explicit assumptions, versioned evaluation, deterministic scenarios, and scenario analysis. A solver time limit can affect reproducibility; limited-search results do not prove optimality. An authorized live feed, richer volatility calibration, and empirical hedge validation are further work. No order routing is provided.
