"""Historical decision cards built from publication records, not invented trades."""
from __future__ import annotations

import numpy as np
import pandas as pd

ARCHIVE = "https://www.eia.gov/petroleum/supply/weekly/archive/"
QUOTES = "https://www.eia.gov/dnav/pet/pet_pri_fut_s1_d.htm"
CONTEXT_2020 = "https://www.eia.gov/todayinenergy/detail.php?id=43495"


def _fmt(value, digits=3, signed=False):
    if value is None or pd.isna(value):
        return "unavailable"
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def _evidence(inventory, observations, start, end):
    if inventory.empty:
        return []
    rows = inventory.loc[inventory.release_date.between(start, end)].copy()
    if rows.empty:
        return []
    rows = rows.sort_values(["release_date", "week_ending"])
    # Features contain the latest observation on each release date; all underlying
    # original reports remain available in the inventory input and source audit.
    quote_fields = ["decision_date", "price_date", "spread", "F2", "F3"]
    quotes = observations.reindex(columns=quote_fields)
    rows = rows.merge(quotes, left_on="release_date", right_on="decision_date", how="left", validate="one_to_one")
    keep = ["report_id", "week_ending", "release_date", "knowledge_cutoff", "cushing_mbbl",
            "inv_z", "state", "inv_delta1", "inv_delta4", "price_date", "spread", "F2", "F3",
            "source_url", "sha256", "reports_on_release_date"]
    records = rows.reindex(columns=keep).replace({np.nan: None}).to_dict("records")
    for row in records:
        row["quote_source_url"] = QUOTES
        row["evidence_id"] = "release-" + row["release_date"]
        row["quote_identity"] = "Public delivery rank; not a fixed-contract return"
    return records


def _known(row):
    return (f"Release {row['release_date']} (week {row['week_ending']}): "
            f"Cushing {_fmt(row['cushing_mbbl'])} million barrels, seasonal z {_fmt(row['inv_z'], 2)}, "
            f"four-week change {_fmt(row['inv_delta4'], 3, True)} million barrels. "
            f"The prior public quote on {row['price_date'] or 'an unavailable date'} has "
            f"F2-F3 {_fmt(row['spread'], 2, True)} USD/bbl.")


def _anchor(row, label):
    return {**row, "anchor_label": label}


def build_cases(inventory, observations, research=None, ledgers=None):
    """Fixed windows and explicitly retrospective diagnostics; no tuning input."""
    cases = []
    rows = _evidence(inventory, observations, "2020-03-01", "2020-06-30")
    if rows:
        before = [r for r in rows if r["release_date"] < "2020-04-20"]
        focus = before[-1] if before else rows[0]
        case = {
            "id": "2020", "title": "2020: a seasonal score can miss operational urgency",
            "start": "2020-03-01", "end": "2020-06-30", "source_url": CONTEXT_2020,
            "selection": "Fixed March-June window; event anchors selected retrospectively, not a trading rule.",
            "evidence": rows,
            "anchors": [_anchor(rows[0], "Window start"), _anchor(focus, "Last report before April 20"), _anchor(rows[-1], "Window end")],
            "known_then": _known(focus),
            "interpretation": "The large four-week stock build and the already negative spread warrant checking receiving capacity even when the seasonal z-score has not crossed the high-stock threshold. The rate of accumulation and accessible space can matter more than a statistical label.",
            "next_check": "Ask for tank lease commitments, available injection capacity and incoming pipeline nominations for the relevant delivery month. Nominally empty space is insufficient evidence that a trader can receive barrels.",
            "falsifier": "Verified uncommitted space and injection capacity that comfortably absorb scheduled inflows would weaken a local receiving-capacity explanation; broad demand weakness would remain a competing explanation for contango.",
            "commercial_impact": "Use the inventory report to prioritize an operational capacity check and review delivery exposure. The seasonal threshold alone is insufficient to size a calendar-spread position.",
            "model_judgment": "Not a locked out-of-sample forecast case: 2020 belongs to development. No hindsight-selected penalty is presented as a forecast available then.",
            "subsequently_observed": "The May WTI contract traded below zero on April 20. EIA's April 27 analysis linked the event to expiring-contract liquidity and limited uncommitted storage. That later explanation is not part of the April 15 information set; it does not measure an M2-M3 trade.",
            "context_sources": [{"url": CONTEXT_2020, "published_date": "2020-04-27", "role": "Retrospective explanation only; excluded from earlier decision inputs"}],
            "failed_hypothesis": "A normal seasonal inventory label is not proof that physical delivery and storage risks are normal.",
        }
        case["summary"] = " ".join([case["known_then"], case["interpretation"], case["model_judgment"]])
        cases.append(case)

    rows = _evidence(inventory, observations, "2023-07-01", "2023-11-30")
    if rows:
        low = [r for r in rows if r["inv_z"] is not None and r["inv_z"] < -1]
        focus = low[0] if low else rows[0]
        trough = min(rows, key=lambda r: r["cushing_mbbl"])
        quoted = [r for r in rows if r["spread"] is not None]
        peak = max(quoted, key=lambda r: r["spread"]) if quoted else focus
        case = {
            "id": "2023", "title": "2023: low stocks did not pin the spread at its peak",
            "start": "2023-07-01", "end": "2023-11-30", "source_url": ARCHIVE,
            "selection": "Fixed July-November window. First Low state is sequentially observable; inventory trough and spread peak are retrospective diagnostics, not entry signals.",
            "evidence": rows,
            "anchors": [_anchor(rows[0], "Window start"), _anchor(focus, "First Low seasonal state"), _anchor(peak, "Highest prior-quote spread: hindsight"), _anchor(trough, "Lowest inventory: hindsight"), _anchor(rows[-1], "Window end")],
            "known_then": _known(focus),
            "subsequently_observed": (f"The window's highest matched spread was {_fmt(peak['spread'], 2, True)} USD/bbl "
                f"on quote date {peak['price_date']} (release {peak['release_date']}). At the later inventory trough "
                f"of {_fmt(trough['cushing_mbbl'])} million barrels, released {trough['release_date']}, "
                f"the preceding quote was {_fmt(trough['spread'], 2, True)} USD/bbl. "
                "These are changing rank-price levels, not an investable holding-period return."),
            "interpretation": "Falling stocks and a positive nearby spread are consistent with prompt scarcity. Their peaks need not coincide: expectations about replenishment or wider oil balances can change while current inventory remains low. The public series cannot identify which explanation dominated.",
            "next_check": "Prioritize dated inbound/outbound pipeline nominations, maintenance schedules and nearby physical differentials. They can distinguish a persistent shortage of deliverable barrels from a temporary low stock reading that is expected to rebuild.",
            "falsifier": "Confirmed sustained inflows and replenishment would weaken a persistent-scarcity view; continuing draws and constrained inflows would challenge a rapid-normalization view. These flow observations are not in the current dataset.",
            "commercial_impact": "Separate a low stock level from an expectation of further spread strengthening. A market-only benchmark is necessary before attributing incremental value to the inventory report.",
            "model_judgment": "Actual-contract out-of-sample predictions are unavailable in the public edition; this card does not reconstruct them from rank quotes.",
            "failed_hypothesis": "Low current inventory alone does not fix the curve level or establish that the spread must keep strengthening.",
        }
        case["summary"] = " ".join([case["known_then"], case["subsequently_observed"], case["commercial_impact"]])
        cases.append(case)

    rows = _evidence(inventory, observations, "2019-01-01", "2019-12-31")
    if rows:
        sample = pd.DataFrame(rows).dropna(subset=["inv_z", "spread"])
        rho = sample.inv_z.corr(sample.spread, method="spearman") if len(sample) > 1 else None
        case = {
            "id": "2019", "title": "2019: the pooled relationship has a weak year",
            "start": "2019-01-01", "end": "2019-12-31", "source_url": ARCHIVE,
            "selection": "Post-review counterexample chosen after seeing annual correlations. Descriptive only; not a held-out discovery.",
            "evidence": rows, "anchors": [_anchor(rows[0], "Year start"), _anchor(rows[-1], "Year end")],
            "known_then": "Each publication and preceding quote is retained separately. The full-year statistic below became available only after the year ended.",
            "subsequently_observed": f"Across {len(sample)} matched 2019 releases, within-year Spearman rho is {_fmt(rho, 3)}. This is a weak association, not evidence of a reliably reversed relationship.",
            "interpretation": "A strong pooled relationship can coexist with an uninformative year. Differences across years and changing physical conditions can contribute to the overall pattern.",
            "next_check": "Compare within-year stock coverage and curve conditions before transferring the pooled relationship to a current decision. Verify whether the inventory regime has enough observations across different years.",
            "falsifier": "A stable relationship across independent periods and comparable inventory states would weaken the instability concern. One weak year does not by itself disprove every storage mechanism.",
            "commercial_impact": "Treat inventory as context for investigating physical balances, with market-only forecasts as the benchmark. Do not turn a pooled scatterplot into a standing long/short rule.",
            "failed_hypothesis": "The historical pooled association is not uniformly informative in every calendar year.",
            "model_judgment": "A descriptive counterexample; it does not replace the future mechanically selected maximum B3 forecast error.",
        }
        case["summary"] = " ".join([case["subsequently_observed"], case["interpretation"], case["commercial_impact"]])
        cases.append(case)

    if research is not None:
        predictions = pd.DataFrame(research.get("predictions", []))
        if not predictions.empty:
            _attach_actual(cases, predictions, ledgers or [], inventory, observations)
    return cases


def _attach_actual(cases, predictions, ledgers, inventory, observations):
    test = predictions.loc[predictions.partition.eq("test")].copy()
    b3 = test.loc[test.model.eq("B3")].sort_values(["decision_date", "event_id"]).copy()
    baseline = next((l for l in ledgers if l.get("partition") == "test" and l.get("model") == "B3"
                     and l.get("cost_ticks") == 1 and not l.get("force_roundtrip")
                     and l.get("scenario", "base") == "base"), {})
    for case in cases:
        if case["id"] != "2023":
            continue
        rows = test.loc[test.decision_date.between(case["start"], case["end"])]
        case["actual_predictions"] = rows.to_dict("records")
        ids = set(rows.event_id)
        case["actual_intervals"] = [r for r in baseline.get("intervals", []) if r["event_id"] in ids]
        case["contract_contributions"] = [r for r in baseline.get("contract_intervals", []) if r["event_id"] in ids]
        case["model_judgment"] = (f"Validated actual-contract test records available for {rows.event_id.nunique()} decisions in this window; inspect B1/B3 forecasts and same-pair outcomes below."
                                  if len(rows) else "No eligible actual-contract test decisions in this fixed window; see event exclusions.")
    if b3.empty:
        return
    worst = b3.loc[(b3.y-b3.prediction).abs().idxmax()]
    ordered = sorted(inventory.release_date.unique()) if len(inventory) else sorted(b3.decision_date.unique())
    j = ordered.index(worst.decision_date)
    start, end = ordered[max(0, j-4)], ordered[min(len(ordered)-1, j+4)]
    interval = next((r for r in baseline.get("intervals", []) if r["event_id"] == worst.event_id), {})
    contributions = [r for r in baseline.get("contract_intervals", []) if r["event_id"] == worst.event_id]
    q = interval.get('quantity')
    position = ('Flat; no position' if q == 0 else 'Long one near / short one far' if q == 1
                else 'Short one near / long one far' if q == -1 else 'Ledger interval unavailable')
    position_sentence = f"Position: {position}. Net interval P&L: {_fmt(interval.get('net_pnl'), 2, True)} USD."
    cases.append({
        "id": "failure", "title": "Largest B3 test forecast error", "start": start, "end": end,
        "source_url": "", "selection": "Maximum absolute B3 test error; first chronological event wins ties. Window includes up to four " + ("EIA releases" if len(inventory) else "eligible test decisions") + " each side.",
        "decision_date": worst.decision_date, "event_id": worst.event_id,
        "near_contract": worst.near_contract, "far_contract": worst.far_contract,
        "prediction": float(worst.prediction), "actual": float(worst.y),
        "position": position, "quantity": q, "net_pnl": interval.get('net_pnl'),
        "evidence": _evidence(inventory, observations, start, end),
        "actual_predictions": test.loc[test.decision_date.between(start, end)].to_dict("records"),
        "actual_intervals": [interval] if interval else [], "contract_contributions": contributions,
        "known_then": f"Decision {worst.decision_date}; fixed pair {worst.near_contract} / {worst.far_contract}; inventory z {_fmt(worst.get('inv_z'), 2)}.",
        "model_judgment": f"B3 predicted {_fmt(worst.prediction, 4, True)} USD/bbl.",
        "subsequently_observed": f"The same-pair target was {_fmt(worst.y, 4, True)} USD/bbl. Largest forecast error need not be the largest trade loss.",
        "summary": f"Decision {worst.decision_date}: predicted {_fmt(worst.prediction, 4)}, actual {_fmt(worst.y, 4)} USD/bbl. {position_sentence} Mechanically selected after evaluation, not a tuning input.",
        "failed_hypothesis": "The model's conditional spread estimate did not describe this realized interval well; the error alone does not identify its physical cause.",
        "commercial_impact": "Review the stored position, two-leg cash flows and omitted physical developments before attributing this failure to inventory itself.",
    })
