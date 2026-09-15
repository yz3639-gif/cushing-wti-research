# Portfolio guide

## Start here

Read the [four-page memo](../outputs/research_memo.pdf) for the argument. Use the [interactive report](../outputs/report.html) to inspect individual releases and the [notebook](../research_walkthrough.ipynb) to follow the calculations. Download the HTML and open it locally to use its embedded charts.

The current edition answers a **historical mechanism question**: how seasonal Cushing inventories relate to nearby WTI spread levels. It also implements the planned forecasting and accounting workflow, whose empirical results await verified actual-month contract data.

## Three-minute walkthrough

### 0:00–0:35 — The commercial question

“Cushing matters because WTI delivery is linked to its physical infrastructure. Low inventory can make immediately available oil more valuable, while rapid builds can raise questions about storage and receiving capacity. I wanted to examine when public inventory information helps explain the nearby curve—and when that intuition breaks down.”

Show the question and the spread definition in the memo. A positive near-minus-far spread means that nearby delivery is more expensive on this part of the curve.

### 0:35–1:10 — What the available data can answer

“I reconstructed inventory observations from EIA's publication-period archives and used only information available by each release cutoff. The public price data identifies delivery ranks rather than persistent monthly contracts. I therefore use it to study historical spread levels, and reserve the forecasting and trade-accounting claims for verified contract data.”

Open one source record. Point out its observation week, publication date and preceding quote date. Explain that the paired quote predates the inventory release: the analysis does not measure a market reaction to newly released information.

### 1:10–1:50 — The finding and its strongest qualification

“Across 481 matched releases, the rank correlation is −0.729: lower seasonal inventories coincide with stronger spreads. But the fixed nonlinear extension gives a joint p-value of 0.775 after year and season controls. Other specifications change the conclusion. The evidence supports inventory as useful market context, while the additional nonlinear shape is uncertain.”

Show the descriptive relationship, then the complete sensitivity table. Say explicitly that the nonlinear extension followed inspection of the initial summaries and is exploratory. Its in-sample fit is not a forecast test.

### 1:50–2:30 — A case that limits the intuition

“In April 2020, the inventory score was still in its Normal seasonal state after a four-week build of 16.520 million barrels. That label could not tell a trader how much uncommitted storage or receiving capacity remained. The next useful information would be terminal availability, injection constraints and incoming flows.”

Show the April 15 release record. Its preceding quote is dated April 14. Distinguish this M2−M3-related public curve analysis from the expiring M1 delivery event, and identify the later EIA explanation as retrospective context.

If time permits, add the 2023 case: the spread peak preceded the inventory trough. In 2019, the within-year association was weak despite the strong pooled pattern.

### 2:30–3:00 — The next test

“The next step is to verify actual monthly settlements and expiry dates, then run the fixed market-only versus inventory-augmented comparison. The ledger follows the same contracts through each holding period and charges actual changes in each leg's position. Until those data pass validation, I do not claim predictive improvement or trading profits.”

Finish with the two open questions: incremental forecasting information and economic value after costs. The [research protocol](RESEARCH_PROTOCOL.md) fixes their evaluation design.

## Where to inspect the evidence

| Question | Evidence to open | What to check |
|---|---|---|
| Are the results based on real observations? | [Data audit](../outputs/data_audit.json) and source links in the report | Original URL, observation week, release date and checksum |
| Does the headline summarize the actual sample? | [Results](../outputs/results.json) and [mechanism results](../outputs/mechanism_results.json) | 481 matched releases; January 2015–April 2024; evidence status |
| Is the nonlinear result stable? | [Mechanism methods](MECHANISM_EXTENSION.md) and report sensitivity tables | Year controls, all deleted-year checks and block lengths 4, 8 and 13 |
| Can a seasonal score miss operational stress? | [2020 case records](../outputs/case_2020_evidence.csv) | Four-week build, Normal state and what the public data cannot observe |
| Do low stocks identify the spread peak? | [2023 case records](../outputs/case_2023_evidence.csv) | Separate dates for spread peak and inventory trough |
| Does the relationship persist within every year? | [2019 case records](../outputs/case_2019_evidence.csv) | Weak within-year association versus the pooled result |
| How is look-ahead avoided in the planned forecast test? | [Technical appendix](TECHNICAL_APPENDIX.md) | Publication cutoff, known settlements, complete labels and fitting gap |
| Where would trading profit come from? | [Accounting methods](TECHNICAL_APPENDIX.md#trade-accounting) and ledger tests | Previous holdings × settlement change; costs on actual position changes |
| Can another researcher reproduce it? | [README instructions](../README.md#reproduce) and [run manifest](../outputs/run_manifest.json) | Fixed inputs, dependencies, configuration and code identity |

## Claims supported by this release

- Reconstructed a source-linked, publication-dated public inventory dataset.
- Found a strong pooled association between seasonal Cushing inventory and preceding nearby WTI spread levels.
- Tested a specified nonlinear extension and retained results that weaken its interpretation.
- Connected statistical limitations to concrete questions about storage, delivery and flows.
- Implemented and tested a fixed-contract forecasting and daily accounting workflow, pending accepted market data.

## Claims this release does not support

- That inventory causes a particular spread movement, or that the analysis isolates the impact of an EIA announcement.
- That the main p-value proves a linear relationship or the absence of an inventory mechanism.
- That the nonlinear analysis was preregistered before inspecting the descriptive evidence.
- That public F2/F3 ranks are persistent, tradable monthly contract identities.
- That the project has established out-of-sample alpha, profitable historical trades or a live trading record.
- That a statistical Low/Normal/High label measures available storage, operating minimum inventory or executable capacity.
- That the nationwide control has been completed; its historical definition requires harmonization.

## Useful follow-up questions

**Why use M2−M3 for the planned trading test?** It preserves exposure to the nearby curve while reducing the influence of the front contract's immediate delivery conditions. It does not eliminate delivery, liquidity or execution risk.

**Why is a strong correlation insufficient?** The public analysis relates inventory to an already observed spread level. A trading decision needs evidence about a subsequent change, beyond information already visible in prices, with a consistent execution and cost model.

**What would most challenge the interpretation?** The sensitivity to year composition already does. The 2019 case is a useful reminder that the aggregate pattern offers limited guidance in some periods. For the next stage, an inventory model that fails to improve paired out-of-sample errors would directly weaken H2.

**What data should come next?** Actual-contract settlements, expiry dates and the settlement calendar enable the existing forecast and ledger protocol. Operational storage availability and flow information would address a different gap: why the same inventory score may imply different physical risks.
