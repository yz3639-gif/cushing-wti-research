# Data sources, provenance and coverage

**Snapshot cutoff:** September 14, 2026. **Author of analysis:** Yuang Zuo.

| Input | Source and scope | Interpretation |
|---|---|---|
| Original weekly stocks | [EIA WPSR archives](https://www.eia.gov/petroleum/supply/weekly/archive/), 789 observation weeks from July 29, 2011 to September 4, 2026; latest release September 10, 2026 | Publication-period values, in million barrels |
| Historical-series cross-check | [EIA Cushing series](https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=W_EPC0_SAX_YCUOK_MBBL&f=W) | Diagnostic later vintage; never replaces historical decision inputs |
| Public delivery-rank quotes | [EIA NYMEX futures prices](https://www.eia.gov/dnav/pet/pet_pri_fut_s1_d.htm), ending April 5, 2024 | F2/F3 describe successive delivery months; not persistent contracts |
| Rank definitions | [EIA definitions, sources and notes](https://www.eia.gov/dnav/pet/TblDefs/pet_pri_fut_tbldef2.asp) | EIA identifies NYMEX as the crude-futures source |
| Release timing | [EIA publication schedule](https://www.eia.gov/petroleum/supply/weekly/schedule.php) and archive dates | Actual dates, including delayed/simultaneous releases |
| Contract rules | [CME NYMEX Chapter 200](https://www.cmegroup.com/rulebook/NYMEX/2/200.pdf) | Contract size, tick and expiry-rule reference; not a verified expiry table |
| 2020 context | [EIA April 27, 2020 analysis](https://www.eia.gov/todayinenergy/detail.php?id=43495) | Retrospective explanation, excluded from earlier information sets |
| National scope change | [EIA October 11, 2016 explanation](https://www.eia.gov/todayinenergy/detail.php?id=28292) | Lease-stock break; national auxiliary test disabled pending harmonization |

## What is included

The repository retains the public statistical source snapshot, processed tables, provenance sidecars, computed evidence and research code. Source content remains unmodified; every mandatory raw file is checked against its audit and sidecar. All 801 expected raw sources and 3 processed tables must pass before a build. No Bloomberg exports or authenticated source URLs are included.

The public relationship sample has 481 matched releases from January 7, 2015 through April 3, 2024. Each new inventory observation is paired with a quote strictly before publication. It does not measure a release reaction or a fixed-contract holding return.

## Reuse and attribution

Source: U.S. Energy Information Administration, dated reports and tables listed above and in each source record. Follow [EIA's reuse policy](https://www.eia.gov/about/copyrights_reuse.php). The repository's project ownership notice does not grant rights to third-party exchange data, trademarks, photographs or any future licensed terminal exports. Original source and embedded software notices are preserved.

## Known source exceptions

- Malformed archive rows are discovered by cells so valid weeks are retained.
- July 3, 2019 uses the same-publication PDF after an incorrect CSV observation header; the PDF's lower printed precision is explicit.
- Two 2020 total-stock reconciliations reflect component rounding; original supporting Table 4 files are retained.
- The October 2016 national lease-stock scope change is preserved and documented; current backcasts do not overwrite original publication inputs.

## Actual-contract study

Historical monthly settlements, actual expiries, a settlement-session calendar and quote-availability provenance remain absent. Follow [the export specification](BLOOMBERG_EXPORT.md) to supply them locally. Input files are ignored by Git. H2/H3 remain untested until their data acceptance succeeds.
