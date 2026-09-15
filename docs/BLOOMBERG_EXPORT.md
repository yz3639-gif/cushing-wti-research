# Actual-contract data checklist

The present report is explicitly a public-data mechanism study. The following inputs activate the individual-contract evaluation after validation. No Bloomberg account or Terminal session is connected to this project.

## 1. Test the source first

On an authorized Terminal, identify the May 2020 NYMEX Light Sweet Crude Oil monthly contract and a normal-period monthly contract. Use the Terminal field dictionary to confirm settlement (not merely last trade), actual last trading date, contract size and price units. Candidate field mnemonics require Terminal verification; this project does not assume a universal mnemonic.

Export a small daily sample and check that the May 2020 contract's April 20, 2020 settlement is -37.63 USD/barrel and its last trading day is April 21. Retain the field description and source notes. A price of zero or below is valid.

Official reference: https://www.cftc.gov/PressRoom/SpeechesTestimony/berkovitzstatement050720

## 2. Export the required panel

Export individually dated monthly CL contracts covering the first four unexpired months at each decision from 2015 through the configured cutoff, plus sufficient 2014 daily history for the initial 20-session features. Include calendar sessions through the actual expiry dates needed for exit protection.

Use the Bloomberg Excel historical-data interface or an authorized API. Export values, not a workbook dependent on a live Terminal. Long-form CSV is preferred. Preserve the source security identifier and a four-digit delivery year to avoid ambiguous ticker years.

Do not supply only continuous CL1/CL2/CL3 series. They do not prove the identities of the contracts held through a period.

## 3. Normalize into these inputs

`data/input/contracts.csv`

```csv
contract_id,delivery_month,last_trade_date,multiplier,tick_size,exchange,currency
```

One row per actual monthly contract. `delivery_month` is YYYY-MM, `last_trade_date` is YYYY-MM-DD. Use stable actual identifiers. Required CL economics: 1000 barrels, USD 0.01/barrel tick, USD prices. Import verified dates rather than setting expiry verification true for a calendar formula.

`data/input/prices.csv`

```csv
trade_date,contract_id,settlement,volume,open_interest
```

One row per contract/session. `volume` and `open_interest` are optional. Empty settlement is missing, not zero. Preserve zero and negative numbers.

`data/input/sessions.csv`

```csv
trade_date,settlement_time
```

Use an energy futures settlement calendar, not a generic weekday or US equities calendar. `settlement_time` includes its timezone offset and must reflect early settlement sessions where applicable. Export or verify the complete calendar independently of missing price observations; do not infer that a day was a holiday simply because a price is missing.

`data/input/market_metadata.json`

```json
{
  "source": "",
  "settlement_field": "",
  "actual_contracts_verified": false,
  "expiry_dates_verified": false,
  "calendar_verified": false,
  "same_day_settlement_available": false,
  "verification_notes": "",
  "verified_by": ""
}
```

Complete these statements from actual evidence, not to bypass the validator. If same-day availability cannot be established, leave its flag false and the engine applies one extra session of quote lag. The calendar's settlement timestamp is a pricing event; it is not by itself proof of the supplier's publication time.

## 4. Validate and run

From the project folder:

```bash
.venv/bin/python -m cushing_research validate
.venv/bin/python -m cushing_research build --offline
.venv/bin/python -m cushing_research report --offline
```

The existing statistical protocol remains unchanged after import. Input defects are reported explicitly. A missing mark while a contract is held halts full evaluation; it is not removed as an inconvenient trade.

## 中文说明

先确认学校允许导出已到期 CL 月合约。用一个2020年样本核对字段和负价，再批量导出。终端如果只能给连续主力序列，保留现在的机制研究状态，不将它拼成交易收益。后续拿到原始CSV后，按上述三张表和来源说明导入即可，核心模型与账本不需要重写。
