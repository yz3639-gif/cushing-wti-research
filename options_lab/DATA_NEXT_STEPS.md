# Acquiring the first real case without a monthly live subscription

The application is ready for local files; no authorized real options snapshot was acquired during this implementation. The included fixture must stay labeled as synthetic.

## Smallest useful acquisition

Request one short historical window containing exact CL futures for the two relevant months, a financially settled European 7A/B7A calendar-spread option, and European LC/LCE vanilla options on those futures. Include instrument definitions, expiries, ordered spread legs, price scale, multiplier, tick, timestamps, and the source observations. Do not substitute American LO options or continuous futures.

For a snapshot demonstration, same-date settlements can support valuation and implied-volatility calibration, with stated assumptions. They cannot supply an observed bid/ask or an intraday replay. A genuine replay needs synchronized timestamped observations, preferably BBO records and sizes. Preserve vendor raw files and their hashes privately.

## No automatic purchase

Databento's public pricing page, checked on 2026-09-23, advertises $125 in new-user credits and historical usage-based access without a monthly subscription. Eligibility and actual credit balance still need confirmation in the user's account. The displayed credit is not an estimate for this project, and a cost estimate is not an invoice guarantee. Start with exact definitions and a very small window; inspect the estimate and account budget before authorizing a download.

- Pricing: https://databento.com/pricing
- Financial CSO catalog: https://databento.com/catalog/cme/GLBX.MDP3/options/B7A
- CL catalog: https://databento.com/catalog/cme/GLBX.MDP3/futures/CL
- Official sample attempts and outcomes: `examples/data_acquisition_status.json`
- Local import schema and bounded API examples: `examples/import_format.md`

Account registration and vendor licensing need the user's participation. Do not paste API keys into chat, checked-in files, or command history. The optional adapter reads `DATABENTO_API_KEY` only from the local process environment. No request runs merely by opening the app.

An existing university terminal or a permitted export from a data provider is another route only if the user's access and software-use rights are confirmed. No university access or vendor entitlement has been assumed here.

## Evidence milestones

1. Verify actual definitions, source files, scales and as-of alignment; calibrate the first accepted real snapshot.
2. Record the existing three-minute interaction again with that source explicitly labeled.
3. Acquire at least 120 valid trading sessions. Keep the final 40 out of model selection, compare common exposures/costs, and report failures and dependent-data uncertainty. The original ridge benchmark requires fixed contracts. The separate [desk-policy runner](DESK_HISTORY.md) uses the same optimizer as the UI and supports a predeclared schedule of exact targets across fresh episodes; it rejects identity substitution and expiry crossings within an episode. Continuous rollover and exchange-calendar acceptance remain separate work.
4. Connect an entitled feed and complete a separate 60-minute run with actual disconnection/resynchronization evidence. The optional transport and simulated recovery test do not complete that milestone.
