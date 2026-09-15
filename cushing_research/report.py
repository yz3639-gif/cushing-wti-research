"""Publication-style research reports driven by a single verified result object.

The public-evidence branch never synthesizes predictions, trades or performance.
HTML is self-contained, including its Plotly runtime; PDF is exactly four pages.
"""
from __future__ import annotations

import html
import json
import math
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", str(Path(tempfile.gettempdir()) / "cushing_research_mpl"))

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import plotly.graph_objects as go
from plotly.offline import get_plotlyjs
from plotly.subplots import make_subplots
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

NAVY = "#173444"
TEAL = "#237F78"
RUST = "#BD604A"
PAPER = "#FAF9F5"
MUTED = "#64747B"
LINE = "#DCE2DF"
BLUE = "#557E96"
STATE_COLORS = {"Low": TEAL, "Normal": BLUE, "High": RUST, "Unavailable": "#AAAAAA"}
TITLE = "Cushing Inventory Stress"
SUBTITLE = "Physical balances and the WTI calendar spread"
PUBLIC_QUESTION = ("How does Cushing inventory relative to its seasonal history relate to the WTI curve, "
                   "and where does that relationship break down?")
PUBLIC_PDF_QUESTION = "How does seasonal inventory relate to the WTI curve, and where does the relationship fail?"
FORECAST_QUESTION = "Does inventory improve spread forecasts beyond market information?"
INVENTORY_DEFINITION = ("z = (reported inventory - historical seasonal mean) / historical seasonal standard deviation. "
    "The reference uses the previous three calendar years, within 28 days either side of the same point in the year, "
    "and at least 20 observations known by the release cutoff. Low: z < -1; High: z > 1; Normal: -1 to +1. "
    "Low means below the historical seasonal reference, not a physical operating minimum or a capacity utilization measure.")
EXPLORATION_DISCLOSURE = ("Post-review exploratory extension. The pooled and annual descriptive results had already been inspected. "
    "D1/D2 and their diagnostic checks describe this historical sample; they are not an untouched test set, a causal estimate or a return forecast.")


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _fmt(value: Any, digits: int = 2, signed: bool = False) -> str:
    n = _number(value)
    if n is None:
        return "Unavailable"
    return f"{n:+,.{digits}f}" if signed else f"{n:,.{digits}f}"


def _pct(value: Any) -> str:
    n = _number(value)
    return _fmt(n * 100, 1) + "%" if n is not None else "Unavailable"


def _pvalue(value: Any) -> str:
    n = _number(value)
    return "Unavailable" if n is None else f"{n:.3e}" if 0 < n < .0001 else f"{n:.4f}"


def _date(value: Any) -> str:
    raw = str(value or "")[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").strftime("%d %b %Y")
    except ValueError:
        return raw or "Unavailable"


def _records(value: Any) -> list[dict]:
    return [r for r in value if isinstance(r, dict)] if isinstance(value, list) else []


def _plain(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value if value is not None else "Unavailable")


def _safe_json(value: Any) -> str:
    def normalize(item: Any) -> Any:
        if isinstance(item, dict):
            return {str(k): normalize(v) for k, v in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(v) for v in item]
        if isinstance(item, (float, np.floating)):
            return float(item) if math.isfinite(item) else None
        if isinstance(item, np.integer):
            return int(item)
        if isinstance(item, (str, int, bool)) or item is None:
            return item
        return str(item)
    return (json.dumps(normalize(value), ensure_ascii=False, allow_nan=False)
            .replace("&", "\\u0026").replace("<", "\\u003c")
            .replace(">", "\\u003e").replace("\u2028", "\\u2028")
            .replace("\u2029", "\\u2029"))


def _safe_url(url: Any) -> str:
    raw = str(url or "")
    return raw if raw.startswith(("https://", "http://")) else ""


def _first_value(row: dict, names: list[str]) -> Any:
    return next((row[k] for k in names if k in row and row[k] is not None), None)


def _context(result: dict) -> dict:
    status = result.get("status", "public_evidence_only")
    if status not in {"public_evidence_only", "full_research"}:
        raise ValueError(f"Unsupported report status: {status}")
    inventory = sorted(_records(result.get("inventory")), key=lambda r: (str(r.get("release_date", "")), str(r.get("week_ending", ""))))
    observations = sorted(_records(result.get("public_observations")), key=lambda r: str(r.get("decision_date", "")))
    desc = result.get("descriptive") or {}
    summary = desc.get("summary") or desc
    latest = inventory[-1] if inventory else {}
    public = status == "public_evidence_only"
    research = result.get("research") or {}
    if not public and not isinstance(result.get("research"), dict):
        raise ValueError("full_research requires a real research result; no placeholder performance is allowed")
    default_headline = ("A documented inventory history and a testable spread hypothesis. Predictive evidence remains pending."
                        if public else "Inventory information tested against market-only forecasts.")
    headline = str(result.get("headline") or default_headline)
    conclusion = str(result.get("conclusion") or (
        "This build establishes the public evidence base. The rank-price relationship is descriptive; it does not establish incremental forecasting value or a tradable edge."
        if public else "Read the paired out-of-sample comparisons and sensitivity checks before interpreting any strategy result."
    ))
    regimes = _records(desc.get("regimes"))
    yearly = _records(desc.get("yearly"))
    span_start = summary.get("start") or (observations[0].get("decision_date") if observations else None)
    span_end = summary.get("end") or (observations[-1].get("decision_date") if observations else None)
    ctx = dict(result=result, public=public, status=status, inventory=inventory,
                observations=observations, desc=desc, summary=summary, latest=latest,
                headline=headline, conclusion=conclusion, regimes=regimes, yearly=yearly,
                research=research, start=span_start, end=span_end,
                n=summary.get("sample_count", len(observations)),
                rho=summary.get("spearman"), cases=_records(result.get("cases")),
                sources=_records(result.get("sources")), mechanism=result.get("mechanism") or {})
    if not public:
        if (research.get("metadata") or {}).get("status") != "complete":
            raise ValueError("full_research requires complete research metadata")
        primary = _primary_comparison(ctx)
        if any(_number(primary.get(k)) is None for k in ("n", "mae_improvement", "ci_low", "ci_high")):
            raise ValueError("full_research requires the paired test B3/B1 comparison with block_size=8, n and interval")
        for model, ticks in (("B1", 1), ("B3", 1), ("B3", 2), ("B3", 4)):
            ledger = _select_ledger(ctx, model, ticks)
            if not ledger or any(_number((ledger.get("summary") or {}).get(k)) is None for k in ("net_pnl", "max_drawdown", "contracts_traded")):
                raise ValueError(f"full_research requires the test {model} {ticks}-tick ledger with net P&L, drawdown and turnover")
            daily = _records(ledger.get("daily"))
            if not daily:
                raise ValueError(f"full_research requires nonempty {model} {ticks}-tick daily ledger")
            if not np.isclose(float(daily[-1].get("cumulative_pnl", np.nan)), float(ledger["summary"]["net_pnl"]), atol=1e-7, rtol=0):
                raise ValueError(f"full_research {model} {ticks}-tick displayed ledger does not match its final net P&L")
            summary = ledger["summary"]
            if summary["contracts_traded"] > 0 and not _records(ledger.get("contract_daily")):
                raise ValueError(f"full_research requires traded-contract daily attribution for {model} {ticks}-tick ledger")
            if "break_even_roundtrip_cost_per_bbl" not in summary or (summary["contracts_traded"] > 0 and _number(summary["break_even_roundtrip_cost_per_bbl"]) is None):
                raise ValueError(f"full_research requires {model} {ticks}-tick break-even cost or explicit zero-turnover state")
        metrics = [row for row in _records(research.get("metrics")) if row.get("partition") == "test" and row.get("scope") == "overall" and row.get("model") in {"B0", "B1", "B2", "B3"}]
        if {row.get("model") for row in metrics} != {"B0", "B1", "B2", "B3"} or any(row.get("n") != primary.get("n") or _number(row.get("mae")) is None for row in metrics):
            raise ValueError("full_research requires common-sample B0-B3 test MAE metrics")
        failure = next((c for c in ctx["cases"] if c.get("id") == "failure"), None)
        if not failure or not failure.get("decision_date") or any(_number(failure.get(k)) is None for k in ("prediction", "actual")):
            raise ValueError("full_research requires the mechanically selected largest B3 test error case")
        intervals = _records(failure.get("actual_intervals"))
        if not intervals or (intervals[0].get("quantity") != 0 and len(_records(failure.get("contract_contributions"))) != 2):
            raise ValueError("full_research failure requires an interval position and both traded-leg contributions")
    return ctx


def _select_ledger(ctx: dict, model="B3", ticks=1) -> dict:
    return next((l for l in _records(ctx["result"].get("ledgers"))
                 if l.get("partition") == "test" and l.get("model") == model
                 and l.get("cost_ticks") == ticks and not l.get("force_roundtrip")
                 and l.get("scenario", "base") == "base"), {})


def _scenario_label(ledger: dict) -> str:
    if ledger.get("scenario", "base") == "extra_execution_day":
        name = "Extra execution-day delay"
    elif ledger.get("force_roundtrip"):
        name = "Forced close and reopen"
    elif ledger.get("cost_ticks", 1) == 1:
        name = "Baseline"
    else:
        name = "Cost stress"
    return f"{ledger.get('partition', '')} / {ledger.get('model', '')} / {name} / {ledger.get('cost_ticks', 1)} tick(s) per leg per side"


def _break_even(summary: dict) -> str:
    if summary.get("contracts_traded") == 0 and summary.get("break_even_roundtrip_cost_per_bbl") is None:
        return "Not defined: zero contract turnover"
    return _fmt(summary.get("break_even_roundtrip_cost_per_bbl"), 4)+" $/bbl"


def _case_by_id(ctx: dict, case_id: str) -> dict:
    return next((c for c in ctx["cases"] if c.get("id") == case_id), {})


def _case_anchor_text(case: dict, index: int = 0) -> str:
    anchors = _records(case.get("anchors"))
    if not anchors:
        return str(case.get("known_then") or case.get("summary") or "No dated source record supplied.")
    row = anchors[min(index, len(anchors)-1)]
    return (f"Released {row.get('release_date')} for week {row.get('week_ending')}: "
            f"{_fmt(row.get('cushing_mbbl'), 3)} million bbl; z {_fmt(row.get('inv_z'), 2)}. "
            f"Quote {row.get('price_date')}: F2-F3 {_fmt(row.get('spread'), 2, True)} $/bbl.")


def _failure_sentence(ctx: dict) -> str:
    c = _case_by_id(ctx, "failure")
    if not c:
        return "The largest B3 test forecast error will be selected only after actual-contract research is available."
    intervals = _records(c.get("actual_intervals"))
    interval = intervals[0] if intervals else {}
    trade = ("The rule was flat; no position was opened." if interval.get("quantity") == 0 else
             f"Position {interval.get('quantity', 'unavailable')} spread unit; interval net P&L {_fmt(interval.get('net_pnl'), 2, True)} USD.")
    return (f"Decision {c.get('decision_date')}: {c.get('near_contract')} / {c.get('far_contract')}. "
            f"B3 forecast {_fmt(c.get('prediction'), 4, True)} versus actual {_fmt(c.get('actual'), 4, True)} $/bbl. "
            f"{trade} Selection: maximum absolute B3 test error, not maximum trading loss.")


def _mechanism_sentence(ctx: dict) -> str:
    m = ctx["mechanism"]
    if not m:
        return "The controlled nonlinearity exploration has not been supplied; state medians alone do not establish nonlinearity."
    finding = (m.get("conclusion") or {}).get("finding") or "The specified shape comparison is unavailable."
    test = m.get("main_test") or {}
    return (str(finding) + f" D2 versus D1 joint HAC test: p = {_pvalue(test.get('p_value'))}; "
            f"common n = {_fmt((m.get('sample') or {}).get('n'), 0)}. "
            "The test concerns the historical spread level after year and seasonal controls.")


def _sensitivity_sentence(ctx: dict) -> str:
    m = ctx["mechanism"]
    no_year = (((m.get("models") or {}).get("D2_no_year_fe") or {}).get("joint_test") or {}).get("p_value")
    deletions = {r.get("excluded_year"): (r.get("main_test") or {}).get("p_value") for r in _records(m.get("leave_one_year_out"))}
    return (f"Without year intercepts, p = {_pvalue(no_year)}; excluding 2018, p = {_pvalue(deletions.get(2018))}; "
            f"excluding 2020, p = {_pvalue(deletions.get(2020))}. These diagnostics expose dependence on controls and sample composition. "
            "Non-significance does not prove a linear mechanism. All year deletions and block sensitivities are retained.")


def _slope_rows(ctx: dict) -> list[dict]:
    m = ctx["mechanism"]
    d2 = (m.get("models") or {}).get("D2") or {}
    blocks = ((m.get("bootstrap") or {}).get("8") or {}).get("models") or {}
    boot = (blocks.get("D2") or {}).get("slopes") or {}
    support = (m.get("sample") or {}).get("tail_support") or {}
    rows = []
    for state in ("Low", "Normal", "High"):
        slope = (d2.get("slopes") or {}).get(state) or {}
        b = boot.get(state) or {}
        n = support.get(state) or {}
        rows.append({"state": state, "slope": slope.get("estimate"), "ci_low": b.get("ci_lower"),
                     "ci_high": b.get("ci_upper"), "n": n.get("n"), "years": n.get("years_n"),
                     "status": b.get("status", "unavailable")})
    return rows


def _mechanism_evidence_html(ctx: dict) -> str:
    if not ctx["mechanism"]:
        return '<div class="note caution">'+html.escape(_mechanism_sentence(ctx))+'</div>'
    m = ctx["mechanism"]
    fit = [{"model": name, "r_squared": row.get("in_sample_r_squared"),
            "mae": row.get("in_sample_mae"), "rmse": row.get("in_sample_rmse")}
           for name, row in (m.get("models") or {}).items() if name in {"D1", "D2"}]
    deletions = [{"excluded_year": r.get("excluded_year"), "n": r.get("n"),
                  "p_value": (r.get("main_test") or {}).get("p_value"),
                  **{state.lower()+"_slope": ((r.get("models") or {}).get("D2") or {}).get("slopes", {}).get(state, {}).get("estimate") for state in ("Low", "Normal", "High")}}
                 for r in _records(m.get("leave_one_year_out"))]
    sensitivities = []
    for block, data in (m.get("bootstrap") or {}).items():
        for state, row in ((data.get("models") or {}).get("D2") or {}).get("slopes", {}).items():
            sensitivities.append({"block": block, "state": state, "ci_low": row.get("ci_lower"),
                                  "ci_high": row.get("ci_upper"), "valid": row.get("valid_repetitions"), "status": row.get("status")})
    return ('<div class="subsection"><div class="chapter-label">H1 / EXPLORATORY SHAPE CHECK</div>'
        '<h3>Does a bend add information about the historical relationship?</h3><p class="lead">'+html.escape(_mechanism_sentence(ctx))+'</p>'
        '<div class="note caution">'+html.escape(EXPLORATION_DISCLOSURE)+'</div>'
        '<div class="chart-card flow"><h3>Linear and fixed-knot descriptions</h3><div id="mechanism-chart" class="plot"></div>'
        '<p class="caption">D1: z, year intercepts and seasonal sine/cosine. D2 adds bends fixed at z = -1 and +1. Lines hold year and seasonal controls at sample means. These fitted levels are historical descriptions, not predicted future changes.</p></div>'
        +_table(_slope_rows(ctx), [("state", "D2 state"), ("slope", "Slope $/bbl per z"), ("ci_low", "95% lower"), ("ci_high", "95% upper"), ("n", "Observations"), ("years", "Years"), ("status", "Support")])
        +'<p class="caption">Slope intervals use 2,000 moving-block resamples of eight release cycles. Missing intervals remain unavailable when support conditions fail. The joint HAC test addresses whether the two bends are jointly zero; a slope interval answers a different question.</p>'
        +'<div class="note caution flow">'+html.escape(_sensitivity_sentence(ctx))+'</div>'
        +'<details><summary>In-sample fit, year deletions and all block sensitivities</summary>'
        +'<h3>In-sample fit only</h3>'+_table(fit, [("model", "Model"), ("r_squared", "In-sample R squared"), ("mae", "In-sample MAE"), ("rmse", "In-sample RMSE")])
        +'<h3>Remove one year at a time</h3>'+_table(deletions)
        +'<h3>Block-length sensitivity</h3>'+_table(sensitivities, limit=30)
        +'<p class="caption">All fit and sensitivity rows are retained, including weaker or unavailable results. Removing a year does not create an untouched sample. The extra flexibility of D2 can improve in-sample fit without improving forecasts.</p></details></div>')



def _annual_note(ctx: dict) -> str:
    if not ctx["yearly"] or not ctx.get("end"):
        return "Yearly sample counts are retained with each observation group."
    last = max(ctx["yearly"], key=lambda r: int(r.get("year", 0)))
    end = str(ctx["end"])[:10]
    if end[:4] == str(last.get("year")) and end[5:] != "12-31":
        return f"{last.get('year')} is a partial year: n = {_fmt(last.get('n'),0)}, through {_date(end)}."
    return f"Final year {last.get('year')}: n = {_fmt(last.get('n'),0)}."


def _year_label(row: dict, ctx: dict) -> str:
    year = str(row.get("year", ""))
    end = str(ctx.get("end") or "")[:10]
    return year + (" (partial)" if end[:4] == year and end[5:] != "12-31" else "")


def _selected_pdf_limits(ctx: dict) -> list[str]:
    limits = [str(x) for x in ctx["result"].get("limitations", [])]
    selected = []
    for words in [("monthly contract", "actual expir"), ("delivery-rank", "rank price"), ("immutable", "publication-period"), ("working-capacity", "uncommitted", "tank space"), ("national", "lease stock", "lease-stock")]:
        found = next((x for x in limits if any(word in x.lower() for word in words)), None)
        if found and found not in selected:
            selected.append(found)
    for item in limits:
        if len(selected) >= 5:
            break
        if item not in selected:
            selected.append(item)
    return selected or ["Conclusions are limited to the supplied observations and methods."]


def _selected_pdf_sources(ctx: dict) -> list[dict]:
    chosen = []
    for words in [("archive",), ("futures prices",), ("nymex", "cme"), ("2020",), ("storage",), ("lease", "methodology")]:
        match = next((x for x in ctx["sources"] if any(word in str(x.get("title", "")).lower() for word in words) and x not in chosen), None)
        if match:
            chosen.append(match)
    for source in ctx["sources"]:
        if len(chosen) >= 6:
            break
        if source not in chosen:
            chosen.append(source)
    return chosen


def _plot_layout(fig: go.Figure, height: int = 340) -> go.Figure:
    fig.update_layout(template="plotly_white", height=height, autosize=True,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="Arial, sans-serif", size=12, color=NAVY),
                      margin=dict(l=57, r=18, t=30, b=47),
                      hovermode="closest", legend=dict(orientation="h", y=1.12, x=0),
                      modebar=dict(bgcolor="rgba(0,0,0,0)", color=MUTED),
                      hoverlabel=dict(bgcolor=NAVY, font_color="white", bordercolor=NAVY))
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=LINE, automargin=True)
    fig.update_yaxes(gridcolor=LINE, zerolinecolor="#9BAAA8", automargin=True)
    return fig


def _plot_data(ctx: dict) -> dict[str, dict]:
    inventory, obs = ctx["inventory"], ctx["observations"]
    figs: dict[str, go.Figure] = {}
    f = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.12, row_heights=[.58, .42])
    x = [r.get("release_date") for r in inventory]
    custom = [[r.get("week_ending"), r.get("release_date"), r.get("state", "Unavailable")] for r in inventory]
    f.add_trace(go.Scatter(x=x, y=[r.get("cushing_mbbl") for r in inventory], mode="lines",
                          name="Cushing inventory", line=dict(color=TEAL, width=1.6), customdata=custom,
                          hovertemplate="Release %{customdata[1]}<br>Week ending %{customdata[0]}<br>%{y:.2f} million barrels<extra></extra>"), row=1, col=1)
    f.add_trace(go.Scatter(x=x, y=[r.get("inv_z") for r in inventory], mode="lines",
                          name="Seasonal inventory z-score", line=dict(color=BLUE, width=1.35),
                          hovertemplate="%{x|%d %b %Y}<br>Inventory z-score %{y:.2f}<extra></extra>"), row=2, col=1)
    f.update_yaxes(title_text="Million barrels", row=1, col=1)
    f.update_yaxes(title_text="Past-only z-score", row=2, col=1)
    f.update_xaxes(title_text="Publication date", row=2, col=1)
    figs["inventory"] = _plot_layout(f, 450)

    f = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=.13)
    f.add_trace(go.Scatter(x=[r.get("decision_date") for r in obs], y=[r.get("inv_z") for r in obs],
                          mode="lines", name="Known inventory state", line=dict(color=TEAL, width=1.4)), row=1, col=1)
    f.add_trace(go.Scatter(x=[r.get("decision_date") for r in obs], y=[r.get("spread") for r in obs],
                          mode="lines", name="Public F2 - F3 rank spread", line=dict(color=RUST, width=1.4),
                          customdata=[[r.get("price_date"), r.get("F2"), r.get("F3")] for r in obs],
                          hovertemplate="Decision %{x|%d %b %Y}<br>Known price date %{customdata[0]}<br>F2 %{customdata[1]:.2f}; F3 %{customdata[2]:.2f}<br>Spread %{y:.3f} $/bbl<extra></extra>"), row=2, col=1)
    f.update_yaxes(title_text="Inventory z-score", row=1, col=1)
    f.update_yaxes(title_text="F2 - F3 ($/bbl)", row=2, col=1)
    figs["alignment"] = _plot_layout(f, 400)

    f = go.Figure()
    for state in ["Low", "Normal", "High"]:
        selected = [r for r in obs if r.get("state") == state and _number(r.get("inv_z")) is not None and _number(r.get("spread")) is not None]
        f.add_trace(go.Scatter(x=[r["inv_z"] for r in selected], y=[r["spread"] for r in selected], mode="markers",
                              name=state, marker=dict(color=STATE_COLORS[state], size=5, opacity=.55),
                              customdata=[[r.get("decision_date"), r.get("price_date")] for r in selected],
                              hovertemplate="%{customdata[0]}<br>Known price %{customdata[1]}<br>Inventory z-score %{x:.2f}<br>Rank spread %{y:.3f} $/bbl<extra></extra>"))
    f.update_xaxes(title="Past-only seasonal inventory z-score")
    f.update_yaxes(title="Public F2 - F3 rank spread ($/bbl)")
    figs["relationship"] = _plot_layout(f, 345)

    f = go.Figure()
    for r in ctx["regimes"]:
        med, q1, q3 = (_number(r.get(k)) for k in ["median_spread", "q25", "q75"])
        if med is None:
            continue
        state = str(r.get("state", "Unavailable"))
        f.add_trace(go.Scatter(x=[state], y=[med], name=state, mode="markers",
                              marker=dict(size=13, color=STATE_COLORS.get(state, BLUE)),
                              error_y=dict(type="data", symmetric=False,
                                           array=[max(0, q3-med) if q3 is not None else 0],
                                           arrayminus=[max(0, med-q1) if q1 is not None else 0], thickness=2, width=8),
                              customdata=[[r.get("n"), q1, q3]],
                              hovertemplate="%{x}<br>Median %{y:.3f} $/bbl<br>IQR %{customdata[1]:.3f} to %{customdata[2]:.3f}<br>n = %{customdata[0]}<extra></extra>"))
    f.update_yaxes(title="F2 - F3 ($/bbl)")
    f.update_layout(showlegend=False)
    figs["regimes"] = _plot_layout(f, 310)

    f = go.Figure(go.Bar(x=[_year_label(r, ctx) for r in ctx["yearly"]], y=[r.get("spearman") for r in ctx["yearly"]],
                        marker_color=[TEAL if (_number(r.get("spearman")) or 0) < 0 else RUST for r in ctx["yearly"]],
                        customdata=[[r.get("n")] for r in ctx["yearly"]],
                        hovertemplate="%{x}<br>Spearman rho %{y:.3f}<br>n = %{customdata[0]}<extra></extra>"))
    f.update_yaxes(title="Within-year Spearman correlation", range=[-1, 1])
    f.update_xaxes(title="Year", type="category")
    figs["stability"] = _plot_layout(f, 295)

    curves = _records(ctx["mechanism"].get("curves"))
    if curves:
        f = go.Figure()
        for model, name, color in (("D1", "D1: linear", BLUE), ("D2", "D2: fixed bends", TEAL)):
            f.add_trace(go.Scatter(x=[r.get("z") for r in curves], y=[r.get(model) for r in curves],
                                  mode="lines", name=name, line=dict(color=color, width=2.4),
                                  hovertemplate="z %{x:.2f}<br>Fitted level %{y:.3f} $/bbl<extra>%{fullData.name}</extra>"))
        for knot in (-1, 1):
            f.add_vline(x=knot, line_width=1, line_dash="dot", line_color=MUTED)
        f.update_xaxes(title="Inventory z-score; knots fixed at -1 and +1")
        f.update_yaxes(title="Controlled fitted spread ($/bbl)")
        figs["mechanism"] = _plot_layout(f, 330)

    f = go.Figure()
    if not ctx["public"]:
        all_preds = _records(ctx["research"].get("predictions"))
        for partition, name in (("test", "predictions"), ("recent", "recent_predictions")):
            preds = [r for r in all_preds if r.get("partition") == partition]
            if not preds:
                continue
            f = go.Figure()
            models = list(dict.fromkeys(str(r.get("model")) for r in preds))
            for model in models:
                rows = [r for r in preds if str(r.get("model")) == model]
                if model not in {"B0", "B1", "B2", "B3"}:
                    continue
                f.add_trace(go.Scatter(x=[r.get("decision_date") for r in rows], y=[r.get("prediction") for r in rows],
                                      name=model, mode="lines", visible=True if model == "B3" else "legendonly"))
            actual = [r for r in preds if str(r.get("model")) == ("B3" if "B3" in models else models[0])]
            f.add_trace(go.Scatter(x=[r.get("decision_date") for r in actual], y=[r.get("y") for r in actual],
                                  mode="lines", name="Observed target", line=dict(color=RUST, width=1.2)))
            f.update_yaxes(title="Fixed-pair spread change ($/bbl)")
            figs[name] = _plot_layout(f, 335)
    return {k: json.loads(v.to_json()) for k, v in figs.items()}


def _mpl_style() -> None:
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9, "axes.titlesize": 10,
                         "axes.labelsize": 8.5, "xtick.labelsize": 7.5, "ytick.labelsize": 7.5,
                         "text.color": NAVY, "axes.labelcolor": NAVY, "axes.edgecolor": LINE,
                         "xtick.color": MUTED, "ytick.color": MUTED,
                         "figure.facecolor": PAPER, "axes.facecolor": PAPER,
                         "grid.color": LINE, "grid.linewidth": .65})


def _finish_axes(ax: Any) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.grid(axis="y", zorder=0)
    ax.set_axisbelow(True)


def _png_figures(ctx: dict, output_dir: Path) -> dict[str, Path]:
    _mpl_style()
    target = output_dir / "figures"
    target.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    def save(name: str, fig: Any) -> None:
        path = target / f"{name}.png"
        fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=PAPER)
        plt.close(fig)
        paths[name] = path
    inv, obs = ctx["inventory"], ctx["observations"]
    fig, ax = plt.subplots(figsize=(8.8, 2.65))
    if inv:
        ax.plot([datetime.fromisoformat(str(r["release_date"])[:10]) for r in inv],
                [r.get("cushing_mbbl", np.nan) for r in inv], color=TEAL, lw=1.2)
    else:
        ax.text(.5, .5, "Inventory observations unavailable", ha="center", transform=ax.transAxes)
    ax.set_ylabel("Million barrels")
    ax.set_xlabel("Publication date")
    _finish_axes(ax)
    fig.tight_layout()
    save("inventory_history", fig)

    fig, axes = plt.subplots(1, 2, figsize=(8.8, 3), gridspec_kw={"width_ratios": [1.55, 1]})
    for state in ["Low", "Normal", "High"]:
        rows = [r for r in obs if r.get("state") == state and _number(r.get("inv_z")) is not None and _number(r.get("spread")) is not None]
        axes[0].scatter([r["inv_z"] for r in rows], [r["spread"] for r in rows], s=7,
                        color=STATE_COLORS[state], alpha=.5, label=state, edgecolors="none")
    axes[0].set_xlabel("Seasonal inventory z-score")
    axes[0].set_ylabel("F2 - F3 rank spread ($/bbl)")
    if obs:
        axes[0].legend(frameon=False, fontsize=7, loc="best")
    for i, r in enumerate(ctx["regimes"]):
        med, q1, q3 = (_number(r.get(k)) for k in ["median_spread", "q25", "q75"])
        if med is not None:
            axes[1].errorbar(i, med, yerr=[[max(0, med-q1) if q1 is not None else 0],
                                          [max(0, q3-med) if q3 is not None else 0]],
                             fmt="o", ms=6, capsize=6, color=STATE_COLORS.get(str(r.get("state")), BLUE))
    axes[1].set_xticks(range(len(ctx["regimes"])), [str(r.get("state")) for r in ctx["regimes"]])
    axes[1].set_ylabel("Median and IQR ($/bbl)")
    axes[1].set_title("Distribution, not a confidence interval", fontsize=8)
    for ax in axes:
        _finish_axes(ax)
    fig.tight_layout(w_pad=2.3)
    save("descriptive_relationship", fig)

    fig, ax = plt.subplots(figsize=(8.8, 2.5))
    vals = [_number(r.get("spearman")) for r in ctx["yearly"]]
    ax.bar([_year_label(r, ctx).replace(" (partial)", "\npartial") for r in ctx["yearly"]], [v if v is not None else np.nan for v in vals],
           color=[TEAL if v is not None and v < 0 else RUST for v in vals], width=.65)
    ax.set_ylim(-1, 1)
    ax.set_ylabel("Within-year Spearman rho")
    ax.axhline(0, color=MUTED, lw=.6)
    _finish_axes(ax)
    fig.tight_layout()
    save("annual_stability", fig)

    curves = _records(ctx["mechanism"].get("curves"))
    if curves:
        fig, axes = plt.subplots(1, 2, figsize=(8.8, 3), gridspec_kw={"width_ratios": [1.3, 1]})
        for model, label, color in (("D1", "D1: linear", BLUE), ("D2", "D2: fixed bends", TEAL)):
            axes[0].plot([r.get("z") for r in curves], [r.get(model) for r in curves], label=label, color=color, lw=1.8)
        for knot in (-1, 1):
            axes[0].axvline(knot, color=MUTED, ls=":", lw=.7)
        axes[0].set_xlabel("Seasonal inventory z-score")
        axes[0].set_ylabel("Controlled fitted spread ($/bbl)")
        axes[0].legend(frameon=False, fontsize=8)
        for i, row in enumerate(_slope_rows(ctx)):
            estimate = _number(row.get("slope"))
            lo, hi = _number(row.get("ci_low")), _number(row.get("ci_high"))
            if estimate is not None:
                if lo is not None and hi is not None:
                    axes[1].plot([lo, hi], [i, i], color=STATE_COLORS[row["state"]], lw=2)
                axes[1].scatter([estimate], [i], s=30, color=STATE_COLORS[row["state"]], zorder=3)
        axes[1].axvline(0, color=MUTED, lw=.6)
        axes[1].set_yticks(range(3), ["Low", "Normal", "High"])
        axes[1].invert_yaxis()
        axes[1].set_xlabel("D2 slope: $/bbl per one z")
        axes[1].set_title("95% block-bootstrap intervals", fontsize=8)
        for ax in axes:
            _finish_axes(ax)
        fig.tight_layout(w_pad=2.4)
        save("controlled_relationship", fig)

    if not ctx["public"]:
        rows = _records(ctx["research"].get("metrics"))
        fig, ax = plt.subplots(figsize=(8.8, 2.6))
        plot_rows = [r for r in rows if r.get("scope", "overall") == "overall" and r.get("partition") == "test"
                     and r.get("model") in {"B0", "B1", "B2", "B3"} and _number(_first_value(r, ["mae", "MAE"])) is not None]
        if plot_rows:
            labels = [" / ".join(str(r.get(k, "")) for k in ["partition", "model", "year"] if r.get(k) is not None) for r in plot_rows[:16]]
            ax.barh(labels, [_first_value(r, ["mae", "MAE"]) for r in plot_rows[:16]], color=[TEAL if r.get("model") == "B3" else BLUE for r in plot_rows[:16]], height=.65)
            ax.invert_yaxis()
            ax.set_xlabel("Mean absolute error ($/bbl)")
        else:
            ax.text(.5, .5, "Refer to supplied research comparison tables", ha="center", transform=ax.transAxes)
        _finish_axes(ax)
        fig.tight_layout()
        save("forecast_comparison", fig)
        fig, axes = plt.subplots(2, 1, figsize=(8.8, 3.2), sharex=True, gridspec_kw={"height_ratios": [1.6, 1]})
        for model, color in (("B1", BLUE), ("B3", TEAL)):
            daily = _records(_select_ledger(ctx, model).get("daily"))
            dates = [datetime.fromisoformat(str(r["trade_date"])[:10]) for r in daily]
            axes[0].plot(dates, [r.get("cumulative_pnl") for r in daily], label=model, color=color, lw=1.3)
            if model == "B3":
                axes[1].fill_between(dates, [r.get("drawdown", 0) for r in daily], 0, color=RUST, alpha=.35)
        axes[0].set_ylabel("Cumulative net USD")
        axes[0].legend(frameon=False, fontsize=8)
        axes[1].set_ylabel("B3 drawdown")
        for ax in axes:
            _finish_axes(ax)
        fig.tight_layout()
        save("ledger_comparison", fig)
    return paths


def _mechanism_html() -> str:
    return '''<div class="mechanism"><div><b>01</b><h3>Inventory state</h3><p>Observed barrels at the delivery hub; a seasonal deviation describes how unusual the level is.</p></div><div><b>02</b><h3>Storage and timing</h3><p>Space, access, flows and delivery obligations influence the value of holding or receiving a barrel.</p></div><div><b>03</b><h3>Calendar spread</h3><p>A nearby premium can reflect prompt scarcity. A deferred premium can reflect the cost or difficulty of carrying inventory.</p></div></div><div class="note">Economic hypothesis, not a causal estimate. Reported stock is not freely available tank capacity. Product, lease and flow constraints can change the relationship.</div>'''


def _table(rows: list[dict], fields: list[tuple[str, str]] | None = None, limit: int = 30) -> str:
    if not rows:
        return '<p class="muted">No results supplied for this section.</p>'
    if fields is None:
        keys = list(dict.fromkeys(k for row in rows for k in row))
        fields = [(k, k.replace("_", " ")) for k in keys if not isinstance(next((r[k] for r in rows if k in r), None), (list, dict))][:10]
    cells = []
    for row in rows[:limit]:
        rendered = []
        for key, _ in fields:
            v = row.get(key)
            text = _pvalue(v) if key == "p_value" else _fmt(v, 4) if isinstance(v, float) else _plain(v)
            rendered.append(f"<td>{html.escape(text)}</td>")
        cells.append("<tr>" + "".join(rendered) + "</tr>")
    return ('<div class="table-scroll"><table><thead><tr>' + ''.join(f'<th>{html.escape(label)}</th>' for _, label in fields)
            + '</tr></thead><tbody>' + ''.join(cells) + '</tbody></table></div>'
            + (f'<p class="caption">Showing {limit} of {len(rows)} records. The complete machine-readable research output is retained by the build.</p>' if len(rows) > limit else ''))


COMPARISON_FIELDS = [("partition", "Period"), ("comparison", "Comparison"), ("block_size", "Block"),
                     ("n", "Pairs"), ("baseline_mae", "Baseline MAE"), ("mae", "Candidate MAE"),
                     ("mae_improvement", "MAE improvement"), ("ci_low", "95% lower"),
                     ("ci_high", "95% upper"), ("evidence", "Evidence / status")]
METRIC_FIELDS = [("partition", "Period"), ("model", "Model"), ("scope", "Scope"), ("year", "Year"),
                 ("state", "State"), ("n", "n"), ("mae", "MAE"), ("rmse", "RMSE"),
                 ("direction_accuracy", "Direction accuracy")]
MAIN_METRIC_FIELDS = [("model", "Model"), ("n", "n"), ("mae", "MAE"), ("rmse", "RMSE"),
                      ("direction_accuracy", "Direction accuracy")]


def _robustness_views(ctx: dict) -> dict:
    """Presentation projection only: preserve supplied estimates and coverage."""
    flat, nested, executions = [], [], []
    for row in _records(ctx["research"].get("robustness")):
        name = str(row.get("check") or row.get("name") or "Unnamed supplied check")
        if name == "extra_execution_day":
            ledger = next((l for l in _records(ctx["result"].get("ledgers")) if l.get("partition")=="test" and l.get("model")=="B3" and l.get("scenario")==name), {})
            s = ledger.get("summary") or {}
            executions.append({"check": "Extra execution-day delay", "status": row.get("status") or s.get("status") or "No ledger supplied",
                "common_events": row.get("common_test_events"), "forecast_policy": row.get("forecast_policy"),
                "net_pnl": s.get("net_pnl"), "max_drawdown": s.get("max_drawdown"),
                "contracts_traded": s.get("contracts_traded"), "execution_dates": _records(ledger.get("execution_dates"))})
        elif "comparisons" in row or "metrics" in row or name == "M3-M4":
            coverage = row.get("test_year_coverage") or {}
            nested.append({"name": name, "status": row.get("status", "Not supplied"),
                "coverage": f"Expected years {coverage.get('expected_years', [])}; actual counts {coverage.get('actual_event_counts', {})}; missing years {coverage.get('missing_years', [])}",
                "validation_status": (row.get("validation_coverage") or {}).get("status", "Not supplied"),
                "comparisons": _records(row.get("comparisons")), "metrics": _records(row.get("metrics"))})
        else:
            flat.append({"check": name, **row, "evidence": row.get("evidence") or row.get("status") or row.get("reason") or "Not supplied"})
    return {"flat": flat, "nested": nested, "executions": executions}


def _robustness_html(ctx: dict) -> str:
    view = _robustness_views(ctx)
    parts = ['<h3>Retained robustness and adverse results</h3>', _table(view["flat"], [("check", "Check"), *COMPARISON_FIELDS], limit=100)]
    for section in view["nested"]:
        parts += ['<details><summary>'+html.escape(section["name"])+': '+html.escape(str(section["status"]))+'</summary>',
                  '<p class="caption">'+html.escape(section["coverage"])+'. Validation coverage: '+html.escape(str(section["validation_status"]))+'</p>',
                  _table(section["comparisons"], COMPARISON_FIELDS, limit=100),
                  '<h3>Supplied overall metrics</h3>', _table([r for r in section["metrics"] if r.get("scope")=="overall"], METRIC_FIELDS, limit=100), '</details>']
    for section in view["executions"]:
        parts += ['<details><summary>Extra execution-day delay: '+html.escape(str(section["status"]))+'</summary>',
                  '<p class="caption">'+html.escape(str(section["forecast_policy"] or "No forecast policy supplied"))+'</p>',
                  _table([section], [("common_events", "Common test events"), ("net_pnl", "Net USD"), ("max_drawdown", "Max drawdown USD"), ("contracts_traded", "Contracts traded")]),
                  _table(section["execution_dates"], [("event_id", "Event"), ("original_entry_date", "Original entry"), ("entry_date", "Delayed entry"), ("original_exit_date", "Original exit"), ("exit_date", "Delayed exit")], limit=15), '</details>']
    return ''.join(parts)


def _full_research_html(ctx: dict) -> str:
    research = ctx["research"]
    parts = ['<div class="callout"><span class="kicker">ACTUAL-CONTRACT HISTORICAL RESEARCH</span><h3>Compare forecast skill and economic value separately.</h3><p>'+html.escape(_forecast_sentence(ctx))+'</p></div>',
             '<h3>Locked out-of-sample comparisons</h3>', _table([row for row in _records(research.get("comparisons")) if row.get("partition") == "test"], COMPARISON_FIELDS, limit=100),
             '<h3>Primary test forecast metrics</h3>', _table([row for row in _records(research.get("metrics")) if row.get("partition") == "test" and row.get("scope") == "overall" and row.get("model") in {"B0","B1","B2","B3"}], MAIN_METRIC_FIELDS),
             '<div id="predictions-chart" class="plot"></div><p class="caption">This chart is fixed to the 2022-2025 test partition. No validation or recent observations are joined to this line.</p><div class="event-review"><div class="control-row"><label for="prediction-partition">Evidence period<select id="prediction-partition"><option value="test">2022-2025 primary test</option><option value="recent">2026 recent historical update</option><option value="validation">2019-2021 development-selected reconstruction</option></select></label><label for="prediction-model">Model<select id="prediction-model"></select></label><label for="prediction-event">Decision event<select id="prediction-event"></select></label></div><div id="prediction-detail" class="record-grid"></div><p class="caption">Each row retains its actual contract pair. Outcome information is displayed for ex-post review only; it was not available at the decision cutoff.</p></div>',
             _robustness_html(ctx)]
    if any(row.get("partition") == "recent" for row in _records(research.get("predictions"))):
        parts.append('<details><summary>2026 recent historical update - separate from the primary test</summary><div id="recent-predictions-chart" class="plot"></div><p class="caption">A separate historical update using its frozen recent fit. It is not a real-time live record and does not change the 2022-2025 primary result.</p>'+_table([row for row in _records(research.get("metrics")) if row.get("partition")=="recent" and row.get("scope")=="overall"], METRIC_FIELDS)+_table([row for row in _records(research.get("comparisons")) if row.get("partition")=="recent"], COMPARISON_FIELDS, limit=100)+"</details>")
    parts.append('<details><summary>Development-selected reconstruction and yearly test metrics</summary><p class="caption">Validation predictions use penalties chosen with 2019-2021 development data. These reconstructions are not independent final evidence and were not all available as displayed at the historical decision time.</p>'+_table([row for row in _records(research.get("metrics")) if row.get("partition")=="validation" and row.get("scope")=="overall"], METRIC_FIELDS)+_table([row for row in _records(research.get("metrics")) if row.get("partition")=="test" and row.get("scope")=="year"], METRIC_FIELDS, limit=100)+_table([row for row in _records(research.get("metrics")) if row.get("partition")=="test" and row.get("scope")=="state"], METRIC_FIELDS, limit=100)+"</details>")
    national = (research.get("metadata") or {}).get("national_control") or {}
    if national.get("status") == "pending" or national.get("enabled") is False:
        parts.append('<div class="note caution"><b>National inventory control: pending.</b> ' + html.escape(str(national.get("pending_reason") or "Historical national stock definitions require harmonization before this control is interpreted.")) + '</div>')
    ledgers = _records(ctx["result"].get("ledgers"))
    if ledgers:
        summaries = [{"scenario_name": _scenario_label(l), "break_even_label": _break_even(l.get("summary") or {}), **{k: l.get(k) for k in ["partition", "model", "cost_ticks", "force_roundtrip", "scenario"]}, **(l.get("summary") or {})} for l in ledgers]
        parts += ['<h3>Rule-based ledger diagnostics</h3><p class="muted">Actual-contract historical accounting under stated execution and cost assumptions. This is simulated research, not realized trading performance. Futures P&amp;L is not a return on invested capital unless a capital denominator is explicitly defined.</p><div class="control-row"><label for="ledger-variant">Accounting scenario<select id="ledger-variant"></select></label></div><div id="ledger-summary" class="record-grid"></div><div class="chart-card"><h3>Cumulative net P&amp;L</h3><div id="ledger-pnl-chart" class="plot"></div><p class="caption">Dollar P&amp;L from the supplied daily held-contract ledger, after fees and scenario slippage. No capital denominator is assumed.</p></div><div class="chart-card flow"><h3>Drawdown</h3><div id="ledger-dd-chart" class="plot"></div><p class="caption">Drawdown follows the supplied ledger convention. Cost and accounting choices select complete precomputed ledgers; the report does not invent execution prices.</p></div><div class="event-review"><h3>Trace a day to each contract</h3><div class="control-row"><label for="ledger-day">Settlement day<select id="ledger-day"></select></label></div><div id="ledger-contract-table"></div><div id="ledger-execution-dates"></div><p class="caption">Start positions earn the marked price change. A newly opened leg has no previous settlement and no entry-day mark profit. Net position change differs from total turnover when positions are closed and reopened.</p></div>', _table(summaries, [("scenario_name", "Scenario"), ("net_pnl", "Net USD"), ("max_drawdown", "Max drawdown USD"), ("contracts_traded", "Contracts traded"), ("break_even_label", "Break-even $/bbl"), ("economic_evidence", "Evidence")])]
    else:
        parts += ['<div class="note caution">No trade ledger was supplied. No trading performance is inferred from forecast metrics.</div>']
    return ''.join(parts)


def _pending_html(ctx: dict) -> str:
    return ('<div class="note caution"><b>H2 and H3 remain untested.</b> Actual month-contract settlements, expiries and settlement availability are required for forecast and trading evidence. The public ranks identify curve positions, not fixed contracts. This edition reports no forecast accuracy or trading P&amp;L. The fixed protocol and input specification are retained for that next step.</div>')


def _html_report(ctx: dict, plots: dict[str, dict]) -> str:
    r = ctx["result"]
    e = lambda v: html.escape(str(v if v is not None else "Unavailable"))
    latest = ctx["latest"]
    status = "PUBLIC EVIDENCE / EXPLORATORY MECHANISM STUDY" if ctx["public"] else "ACTUAL-CONTRACT HISTORICAL RESEARCH"
    regime_table = _table(ctx["regimes"], [("state", "Inventory state"), ("n", "Observations"), ("median_spread", "Median $/bbl"), ("q25", "25th pct."), ("q75", "75th pct.")])
    case_buttons = ''.join(f'<button class="case-button" data-case="{i}"><strong>{e(c.get("title"))}</strong><span>{e(_date(c.get("start")) + " - " + _date(c.get("end")))}</span></button>' for i,c in enumerate(ctx["cases"]))
    source_list = ''.join('<li>' + (f'<a href="{e(_safe_url(src.get("url")))}" target="_blank" rel="noopener noreferrer">{e(src.get("title", "Source"))} ↗</a>' if _safe_url(src.get("url")) else e(src.get("title", "Source"))) + f'<p>{e(src.get("role", ""))}</p></li>' for src in ctx["sources"])
    limits = ''.join('<li>'+e(x)+'</li>' for x in r.get("limitations", []))
    research_block = _pending_html(ctx) if ctx["public"] else _full_research_html(ctx)
    if ctx["public"]:
        snapshot = (f'<dt>Matched descriptive releases</dt><dd>{e(_fmt(ctx["n"],0))}</dd><dt>Public rank-price overlap</dt><dd class="smaller">{e(_date(ctx["start"]))} - {e(_date(ctx["end"]))}</dd><dt>Inventory / spread Spearman rho</dt><dd>{e(_fmt(ctx["rho"],3))}</dd>')
        snapshot_note = "Newly reported inventory is paired with an already-existing F2-F3 quote. This measures historical market states."
    else:
        primary = _primary_comparison(ctx)
        snapshot = (f'<dt>Paired test observations</dt><dd>{e(_fmt(primary.get("n"),0))}</dd><dt>B1 minus B3 MAE ($/bbl)</dt><dd>{e(_fmt(primary.get("mae_improvement"),4))}</dd><dt>95% paired interval</dt><dd class="smaller">[{e(_fmt(primary.get("ci_low"),4))}, {e(_fmt(primary.get("ci_high"),4))}]</dd>')
        snapshot_note = "2022-2025 locked primary comparison. The 2026 update remains separate historical research."
    mechanism_answer = ('<p class="answer-detail">'+e(_mechanism_sentence(ctx))+'</p>') if ctx["public"] and ctx["mechanism"] else ''
    if ctx["observations"]:
        descriptive_evidence = f'''<div class="two-chart"><div class="chart-card"><h3>Seasonal inventory and the rank spread</h3><div id="relationship-chart" class="plot"></div><p class="caption">One point per release, paired to the preceding quote. Pooled Spearman rho = {e(_fmt(ctx['rho'],3))}; n = {e(_fmt(ctx['n'],0))}. Annual differences can contribute to this pooled pattern.</p></div><div class="chart-card"><h3>The distribution in each state</h3><div id="regimes-chart" class="plot"></div><p class="caption">Dots are medians; whiskers span the 25th-75th percentiles. These are distributions, not confidence intervals. Ordered medians do not establish nonlinear effects.</p></div></div>{regime_table}'''
        alignment_html = '<div class="chart-card flow"><h3>The two series on one timeline</h3><div id="alignment-chart" class="plot tall"></div><p class="caption">F2-F3 &gt; 0 means the second listed delivery month is dearer than the third. These changing rank labels describe the curve; their changes cannot be treated as fixed-contract holding profits.</p></div>'
    else:
        descriptive_evidence = '<div class="note caution" id="public-evidence-unavailable"><b>Public market-state evidence is unavailable in this result.</b> No matched public inventory and curve observations were supplied, so the historical relationship and state distributions cannot be shown. Missing evidence does not imply a zero spread or no relationship.</div>'
        alignment_html = ''
    if ctx["yearly"]:
        stability_html = f'<div class="chart-card flow"><h3>2019 challenges the pooled interpretation</h3><div id="stability-chart" class="plot"></div><p class="caption">Within-year correlations describe state relationships. A small positive estimate can mean a weak association; it does not establish a reversed mechanism. {e(_annual_note(ctx))}</p></div>'
    else:
        stability_html = '<div class="note caution flow" id="annual-evidence-unavailable">Annual public relationship evidence is unavailable in this result. No yearly descriptive estimates were supplied; no zero correlation is implied.</div>'
    body = f'''<header class="masthead"><div class="shell"><div class="eyebrow">INDEPENDENT COMMODITIES RESEARCH <span>CUSHING, OKLAHOMA / RESEARCH NOTE 01</span></div><div class="hero-layout"><div><h1>{TITLE}</h1><p class="subtitle">{SUBTITLE}</p></div><div class="byline">{e(r.get("author", "Independent researcher"))}<br>Inventory cutoff: {e(_date(r.get("data_cutoff")))}<br><span>{e(r.get("run_id", ""))}</span></div></div><div class="status-strip">{status}</div></div></header>
<nav><div class="shell"><a href="#answer">01 Answer</a><a href="#known">02 What was known</a><a href="#evidence">03 Inventory information</a><a href="#failure">04 Trading & failure</a><a href="#judgment">05 Methods & sources</a><span class="offline">OFFLINE EDITION</span></div></nav>
<main class="shell"><section id="answer"><div class="chapter-label">01 / RESEARCH ANSWER</div><p class="research-question"><b>Question</b> {e(PUBLIC_QUESTION if ctx['public'] else FORECAST_QUESTION)}</p><div class="answer-grid"><div><h2 class="headline">{e(ctx["headline"])}</h2><p class="lead">{e(ctx["conclusion"])}</p>{mechanism_answer}<a class="text-link" href="#evidence">Inspect the evidence ↓</a></div><aside class="summary-box"><div class="kicker">EVIDENCE SNAPSHOT</div><dl>{snapshot}</dl><p>{e(snapshot_note)}</p></aside></div><div class="reading-key"><div><b>Observed</b><span>Original publication records and dated quotes</span></div><div><b>Estimated</b><span>Historical shape and stability, with uncertainty</span></div><div><b>{'Next test' if ctx['public'] else 'Separately tested'}</b><span>Incremental forecasts, costs and contract cash flows</span></div></div></section>
<section id="known"><div class="section-heading"><div><div class="chapter-label">02 / WHAT WAS KNOWN</div><h2>Read the stock, date and quote together.</h2></div><div class="facts-small">Latest Cushing stock: <b>{e(_fmt(latest.get('cushing_mbbl'),3))} million bbl</b><br>Week {e(_date(latest.get('week_ending')))} · released {e(_date(latest.get('release_date')))}</div></div><div class="definition"><h3>What “Low” and “High” mean</h3><p>{e(INVENTORY_DEFINITION)}</p></div><div class="chart-card flow"><div class="chart-title"><h3>Inventory level and seasonal state</h3><div class="range-controls"><button data-range="1Y">1Y</button><button data-range="5Y">5Y</button><button data-range="All" class="selected" aria-pressed="true">All</button></div></div><div id="inventory-chart" class="plot tall"></div><p class="caption">The x-axis is the actual publication date. Missing seasonal features remain missing. Time controls change this exploration view, never the formal results.</p></div><div class="event-review"><div class="control-row"><label for="inventory-record">Inspect an inventory publication<select id="inventory-record"></select></label></div><div id="inventory-detail" class="record-grid"></div><a id="inventory-source" hidden target="_blank" rel="noopener noreferrer">Original publication source ↗</a></div>{_mechanism_html()}<div class="timeline"><span><b>Observation week</b>Physical stock was measured</span><span><b>Preceding quote</b>Curve already existed</span><span><b>Actual publication</b>Inventory became available</span><span><b>Next settlement</b>Future research execution</span></div><p class="caption">Quote and observation-week dates can overlap; their ordering is recorded explicitly. The descriptive pair joins a newly released stock reading to the last valid quote before release, not to a future return.</p></section>
<section id="evidence"><div class="section-heading"><div><div class="chapter-label">03 / DOES INVENTORY ADD INFORMATION?</div><h2>Separate a historical pattern from a forecast.</h2></div><span class="tag">H1: MARKET-STATE EVIDENCE</span></div>{descriptive_evidence}{_mechanism_evidence_html(ctx)}{alignment_html}</section>
<section id="failure"><div class="chapter-label">04 / TRADING & FAILURE</div><h2>Use the exceptions to sharpen the judgment.</h2>{stability_html}<div class="case-layout"><div><h3>Replay a historical question</h3><p class="muted">2020 and 2023 use fixed historical windows. The 2019 counterexample was selected after reviewing annual evidence. Peak and trough anchors are retrospective diagnostics. Each card keeps known facts separate from later outcomes.</p><div class="case-buttons">{case_buttons}</div><button id="clear-case" class="quiet-button">Reset chart window</button></div><div class="case-detail"><span class="kicker">SELECTED CASE</span><h3 id="case-title">Select a case</h3><p id="case-summary">The full-sample conclusions stay fixed when a case is selected.</p><a id="case-source" hidden target="_blank" rel="noopener noreferrer">Read the context source ↗</a></div></div><div id="case-narrative" class="case-narrative"></div><div id="case-anchors"></div><div class="control-row"><label for="case-record">Dated evidence inside this window<select id="case-record"></select></label></div><div id="case-evidence" class="record-grid"></div><a id="case-record-source" hidden target="_blank" rel="noopener noreferrer">Open this original inventory report ↗</a><div id="case-actuals"></div><div class="subsection">{research_block}</div></section>
<section id="judgment"><div class="chapter-label">05 / METHODS & SOURCES</div><h2>Follow every conclusion back to its evidence.</h2><div class="judgment-grid"><article><span>INVENTORY STATE</span><h3>Prioritize the physical check.</h3><p>The 2020 example motivates a check of uncommitted storage and injection capacity, even when a seasonal label is Normal. Statistical history cannot measure operational access.</p></article><article><span>CURVE EXPECTATIONS</span><h3>Ask what will replenish stocks.</h3><p>The 2023 example separates low inventory from continued spread strengthening. Dated flows, maintenance and physical differentials can help distinguish persistent scarcity from an expected rebuild.</p></article><article><span>NEXT RESEARCH DATA</span><h3>Resolve a specific uncertainty.</h3><p>Verified actual contracts enable a forward B1/B3 comparison. Operational records explain why a stock-curve relationship fails. Each dataset answers a different question.</p></article></div><div class="note"><b>Reproduce.</b> Validate the saved inputs, build the fixed results, then render the reports. The HTML, PDF, tables and interview notes use one result object. A source hash checks local snapshot consistency; it cannot prove the official archive was never replaced.</div><div class="closing-grid"><div><h3>Limits that matter</h3><ul class="limit-list">{limits or '<li>Evidence is limited to the supplied data and explicitly stated methods.</li>'}</ul><details><summary>Audit, coverage and frozen configuration</summary><pre id="audit-json"></pre></details><details><summary>Research and data dictionary</summary><p>Inventory: million barrels; z: seasonal standard deviations. Public spread: F2 minus F3 in $/bbl. Actual target: fixed M2/M3 spread change from entry to exit. Descriptive D1/D2 models are separate from forecasting B0-B3. No missing input is filled with a demonstration value.</p><p>{e(EXPLORATION_DISCLOSURE)}</p></details></div><div><h3>Sources and version</h3><ul class="source-list">{source_list}</ul><p class="caption">Author: {e(r.get('author','Independent researcher'))}<br>Generated: {e(r.get('generated_at','Unavailable'))}<br>Result version: {e(r.get('run_id','Unavailable'))}</p><p class="caption">Source terms govern sharing. This report does not grant redistribution rights to restricted terminal data.</p></div></div></section><footer>Independent portfolio research · No affiliation with Glencore or any exchange is implied.<br><span>Observed facts, explicit assumptions and results that can be checked.</span></footer></main>'''
    payload = {"plots": plots, "cases": ctx["cases"], "inventory": ctx["inventory"], "observations": ctx["observations"],
               "mechanism": ctx["mechanism"], "research": ctx["research"], "predictions": [] if ctx["public"] else _records(ctx["research"].get("predictions")),
               "ledgers": [] if ctx["public"] else [{**l, "scenario_name": _scenario_label(l)} for l in _records(r.get("ledgers"))],
               "audit": {"coverage": r.get("coverage", {}), "data_audit": r.get("data_audit", {}), "config": r.get("config", {}), "run_id": r.get("run_id")}}
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<meta name="color-scheme" content="light"><title>'+TITLE+' - Research Report</title><style>' + _CSS + '</style></head><body>'
            + body + '<script>' + get_plotlyjs() + '</script><script id="report-data" type="application/json">'
            + _safe_json(payload) + '</script><script>' + _JS + '</script></body></html>')


_CSS = r'''
:root{--paper:#faf9f5;--ink:#173444;--teal:#237f78;--rust:#bd604a;--muted:#64747b;--line:#dce2df}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--paper);color:var(--ink);font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}button{font:inherit;cursor:pointer}a{color:var(--teal);text-underline-offset:4px}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #e4b66f;outline-offset:4px}h1,h2,h3,p{margin:0}h1,h2{font-family:Georgia,"Times New Roman",serif;font-weight:400}h1{font-size:clamp(37px,4.6vw,61px);line-height:1.08;letter-spacing:-2px}h2{font-size:31px;line-height:1.2;letter-spacing:-.6px}h3{font-size:16px;line-height:1.4}p+p{margin-top:12px}.shell{max-width:1250px;margin:auto;padding:0 38px}.masthead{padding:28px 0 0;background:#f2f1eb;border-bottom:1px solid var(--line)}.eyebrow{font-size:10px;letter-spacing:1.7px;display:flex;justify-content:space-between;font-weight:700;color:var(--teal);margin-bottom:29px}.eyebrow span{color:var(--muted)}.hero-layout{display:flex;justify-content:space-between;align-items:end;gap:25px}.subtitle{font-size:17px;color:var(--muted);margin-top:12px}.byline{text-align:right;font-size:11px;line-height:1.85;min-width:180px}.byline span{color:var(--muted);font-size:9px}.status-strip{margin-top:28px;padding:10px 0;border-top:1px solid #cbd5d2;font-size:10px;letter-spacing:1.2px;font-weight:700;color:var(--rust)}nav{position:sticky;top:0;z-index:10;background:#faf9f5f2;backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}nav .shell{display:flex;gap:25px;align-items:center;overflow-x:auto}nav a{font-size:11px;font-weight:650;color:var(--ink);text-decoration:none;white-space:nowrap;padding:15px 0}.offline{margin-left:auto;white-space:nowrap;font-size:9px;letter-spacing:1.2px;color:var(--muted)}section{padding:40px 0;border-bottom:1px solid var(--line);scroll-margin-top:48px}.chapter-label{font-size:10px;font-weight:700;letter-spacing:1.6px;color:var(--teal);margin-bottom:15px}.research-question{font-size:12px;color:var(--muted);margin:-3px 0 23px}.research-question b{color:var(--ink);margin-right:8px}.answer-grid{display:grid;grid-template-columns:minmax(0,1.8fr) minmax(240px,1fr);gap:47px}.headline{font-size:33px;line-height:1.27;max-width:790px}.lead{margin:19px 0;font-size:15px;line-height:1.85;color:#455b64}.text-link{font-size:12px;font-weight:700;text-decoration:none}.summary-box{padding:24px;background:#fff;border:1px solid var(--line);border-top:3px solid var(--teal)}.kicker{font-size:9px;font-weight:700;letter-spacing:1.3px;color:var(--teal)}dl{margin:17px 0 12px}dt{font-size:11px;color:var(--muted);margin-top:12px}dd{margin:2px 0 0;font-size:27px;line-height:1.35;font-weight:650;font-variant-numeric:tabular-nums;letter-spacing:-.5px}dd.smaller{font-size:13px;letter-spacing:0}.summary-box p{font-size:10px;color:var(--muted);line-height:1.65}.reading-key{display:grid;grid-template-columns:1fr 1fr 1fr;gap:30px;margin-top:29px;padding-top:18px;border-top:1px solid var(--line)}.reading-key b{display:block;font-size:11px;letter-spacing:.4px}.reading-key span{display:block;font-size:11px;color:var(--muted);margin-top:3px}.section-heading{display:flex;justify-content:space-between;align-items:center;gap:20px;margin-bottom:21px}.facts-small{font-size:11px;line-height:1.8;text-align:right;color:var(--muted)}.facts-small b{color:var(--ink);font-weight:600}.chart-card{background:#fff;border:1px solid var(--line);padding:21px 21px 17px;min-width:0}.chart-title{display:flex;justify-content:space-between;align-items:center;gap:15px}.plot{width:100%;min-height:285px;margin-top:7px}.plot.tall{min-height:395px}.plot .modebar{display:none!important}.caption{font-size:11px;line-height:1.7;color:var(--muted);margin-top:9px}.range-controls{display:flex;border:1px solid var(--line);border-radius:4px;overflow:hidden;flex-shrink:0}.range-controls button{border:0;background:transparent;color:var(--muted);font-size:10px;padding:7px 12px;font-weight:650;min-height:34px}.range-controls button.selected{color:white;background:var(--teal)}.mechanism{display:grid;grid-template-columns:1fr 1fr 1fr;gap:27px;margin:27px 0 18px}.mechanism b{color:var(--rust);font-size:10px;letter-spacing:1px}.mechanism h3{margin-top:6px}.mechanism p{font-size:12px;color:var(--muted);margin-top:7px}.note{background:#edf3f0;border-left:3px solid #92b5ad;padding:13px 17px;font-size:12px;line-height:1.8;color:#3f605e}.note.caution{background:#f9eee6;border-color:#c98666;color:#72533f}.two-chart{display:grid;grid-template-columns:minmax(0,1.4fr) minmax(0,1fr);gap:19px}.flow{margin-top:21px}.tag{display:inline-block;border:1px solid #bdcfc8;border-radius:3px;padding:4px 7px;color:var(--teal);font-size:9px;letter-spacing:.6px;font-weight:700;white-space:nowrap}.tag.rust{color:var(--rust);border-color:#ddc0b1}.table-scroll{overflow:auto;margin-top:20px}table{width:100%;border-collapse:collapse;text-align:left;font-size:12px}thead{background:#eef1ec}th{font-size:10px;font-weight:650;color:var(--muted);white-space:nowrap;padding:10px 13px}td{padding:10px 13px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}details{margin-top:19px;padding:12px 0;border-top:1px solid var(--line)}summary{cursor:pointer;color:var(--teal);font-size:12px;font-weight:650}details p{color:var(--muted);font-size:12px;margin-top:11px}.case-layout{display:grid;grid-template-columns:1.15fr 1fr;gap:27px;margin-top:26px}.muted{color:var(--muted);font-size:12px;line-height:1.85;margin-top:8px}.case-buttons{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin:15px 0}.case-button{background:transparent;border:1px solid var(--line);padding:12px;text-align:left;border-radius:4px;min-height:68px;color:var(--ink)}.case-button strong{display:block;font-size:12px;font-weight:650}.case-button span{display:block;font-size:10px;color:var(--muted);margin-top:5px}.case-button.selected{background:#e7f1ed;border-color:#8bb2a8}.case-detail{padding:23px;background:#f0f0e9;border-top:2px solid var(--teal)}.case-detail h3{margin-top:10px}.case-detail p{font-size:12px;line-height:1.8;margin-top:10px;color:#4f636c}.case-detail a{display:inline-block;font-size:11px;margin-top:13px}.quiet-button{background:transparent;border:0;text-decoration:underline;text-underline-offset:4px;font-size:11px;color:var(--teal);padding:6px 0}.subsection{margin-top:32px}.pending-panel{padding:24px 27px;border:1px solid #d8bdaa;border-left:4px solid var(--rust);background:#fcf3eb}.pending-label{font-size:10px;font-weight:750;letter-spacing:1px;color:var(--rust);margin-bottom:10px}.pending-panel h3{font-size:19px}.pending-panel p,.pending-panel li{font-size:12px;line-height:1.8;color:#715441}.pending-panel p{margin-top:10px}.pending-panel ul{margin-bottom:0;padding-left:18px}.evidence-grid,.judgment-grid{display:grid;grid-template-columns:1fr 1fr 1fr;gap:25px;margin:24px 0}.evidence-grid h3{margin:11px 0 7px}.evidence-grid p{font-size:12px;color:var(--muted)}.judgment-grid article{padding:20px 0;border-top:2px solid var(--ink)}.judgment-grid article span{font-size:9px;letter-spacing:1px;font-weight:700;color:var(--teal)}.judgment-grid h3{margin:8px 0}.judgment-grid p{font-size:12px;color:var(--muted)}.closing-grid{display:grid;grid-template-columns:1.3fr 1fr;gap:43px;margin-top:29px}.limit-list{font-size:12px;color:var(--muted);line-height:1.85;padding-left:18px}.limit-list li{margin:8px 0}.source-list{list-style:none;padding:0;margin:12px 0}.source-list li{border-bottom:1px solid var(--line);padding:10px 0}.source-list a{font-size:12px;font-weight:650}.source-list p{font-size:10px;color:var(--muted);margin-top:3px}pre{font:10px/1.7 ui-monospace,SFMono-Regular,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#eeeee8;padding:13px;max-height:390px;overflow:auto}footer{font-size:11px;color:var(--muted);padding:23px 0 32px}footer span{font-size:10px}.callout{padding:22px;background:#e9f1eb;margin-bottom:25px}.callout h3{margin:7px 0}.callout p{font-size:12px;color:var(--muted)}.subsection>h3{margin-top:27px}
.definition{background:#eaf1ec;border-left:3px solid var(--teal);padding:18px 21px}.definition h3{font-size:14px}.definition p{font-size:12px;line-height:1.8;margin-top:7px}.answer-detail{font-size:12px;line-height:1.8;color:var(--muted);margin:0 0 17px}.timeline{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px;border-top:1px solid var(--line);padding-top:17px;margin-top:22px}.timeline span{font-size:11px;color:var(--muted)}.timeline b{display:block;color:var(--ink);font-size:12px;margin-bottom:5px}.case-narrative{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 26px;margin-top:25px}.case-narrative article{border-top:1px solid var(--line);padding-top:13px}.case-narrative h3{font-size:12px;color:var(--teal)}.case-narrative p{font-size:12px;color:#4f636c;line-height:1.85;margin-top:7px}.bounded-table{max-height:470px;overflow:auto}.bounded-table thead{position:sticky;top:0;z-index:1}.case-detail{overflow-wrap:anywhere}#case-actuals h3{margin-top:25px}#inventory-source,#case-record-source{font-size:11px}#case-evidence .record-item:last-child,#inventory-detail .record-item:last-child{grid-column:1/-1}.record-item strong{overflow-wrap:anywhere}.case-layout{align-items:start}.caption{overflow-wrap:anywhere}
@media(max-width:1000px){.shell{padding:0 25px}.answer-grid{gap:28px;grid-template-columns:1.5fr 1fr}.headline{font-size:28px}.two-chart{grid-template-columns:1fr 1fr}.chart-card{padding:16px}.section-heading{align-items:start}nav .shell{gap:19px}.offline{display:none}.mechanism,.evidence-grid,.judgment-grid{gap:18px}}
@media(max-width:720px){.case-narrative{grid-template-columns:1fr}.timeline{grid-template-columns:repeat(2,minmax(0,1fr))}.definition{padding:15px}.shell{padding:0 18px}.masthead{padding-top:22px}.eyebrow{font-size:8px;letter-spacing:1.1px;margin-bottom:20px}.eyebrow span{display:none}.hero-layout{display:block}h1{font-size:40px;letter-spacing:-1.4px}.subtitle{font-size:14px}.byline{text-align:left;margin-top:16px;font-size:10px;line-height:1.6}.status-strip{font-size:8px;margin-top:19px;letter-spacing:.7px}.answer-grid,.two-chart,.case-layout,.closing-grid{grid-template-columns:1fr;gap:20px}.summary-box{padding:18px}.summary-box dl{display:grid;grid-template-columns:1.2fr 1fr;gap:5px 16px;margin:10px 0}.summary-box dt{margin-top:0;align-self:center}.summary-box dd{font-size:22px}.summary-box dd.smaller{font-size:11px}.section-heading{display:block}.facts-small{text-align:left;margin-top:12px;font-size:10px}h2{font-size:28px}.headline{font-size:29px;line-height:1.25}.lead{font-size:14px}.reading-key{gap:14px}.reading-key b,.reading-key span{font-size:10px}.mechanism,.evidence-grid,.judgment-grid{grid-template-columns:1fr;gap:18px}.mechanism>div{padding-left:36px;position:relative}.mechanism b{position:absolute;left:0;top:5px}.mechanism h3{margin-top:0}.chart-card{padding:14px 9px 13px}.chart-card h3,.chart-card .caption{padding:0 5px}.range-controls button{min-height:44px;padding:7px 10px}.chart-title h3{font-size:14px}.plot{min-height:285px}.plot.tall{min-height:375px}.tag{margin-top:12px}.pending-panel{padding:20px}.pending-panel h3{font-size:18px}.case-button{min-height:80px}.quiet-button{min-height:44px}.chapter-label{font-size:9px}section{padding:31px 0}.closing-grid{gap:24px}.judgment-grid article{padding:15px 0 0}.case-detail{padding:20px}nav a{padding:14px 0}th,td{padding:9px 10px}.caption{font-size:10px}.evidence-grid .tag{margin-top:0}}
.control-row{display:flex;gap:16px;margin:17px 0;flex-wrap:wrap}.control-row label{font-size:11px;font-weight:650;color:var(--muted);display:block;flex:1;min-width:170px}.control-row select{display:block;width:100%;min-height:44px;margin-top:6px;padding:8px 10px;border:1px solid var(--line);background:white;color:var(--ink);font:12px -apple-system,BlinkMacSystemFont,sans-serif;border-radius:4px}.record-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:17px 0 22px}.record-item{padding:11px 12px;background:#eef2ed}.record-item span{display:block;font-size:9px;letter-spacing:.4px;text-transform:uppercase;color:var(--muted)}.record-item strong{display:block;font-size:13px;font-variant-numeric:tabular-nums;font-weight:650;margin-top:4px;overflow-wrap:anywhere}.event-review{border-top:1px solid var(--line);padding-top:10px;margin-top:10px}@media print{nav,.range-controls,.case-buttons,.quiet-button{display:none}.shell{max-width:none;padding:0 25px}.masthead{padding-top:20px}.status-strip{color:#8b4939}.chart-card,.summary-box,.pending-panel,.mechanism{break-inside:avoid}section{padding:22px 0}.plot{max-height:400px}.answer-grid{grid-template-columns:1.7fr 1fr}.headline{font-size:26px}.closing-grid{grid-template-columns:1fr 1fr}body{background:white}footer{font-size:9px}}
'''

_JS = r'''
'use strict';
const DATA=JSON.parse(document.getElementById('report-data').textContent);
const ids={inventory:'inventory-chart',alignment:'alignment-chart',relationship:'relationship-chart',regimes:'regimes-chart',stability:'stability-chart',mechanism:'mechanism-chart',predictions:'predictions-chart',recent_predictions:'recent-predictions-chart'};
const config={responsive:true,displayModeBar:false,displaylogo:false,scrollZoom:false};
const plotPromises=[];
for(const [name,figure] of Object.entries(DATA.plots)){
  const id=ids[name];if(!id||!document.getElementById(id))continue;
  const layout={...figure.layout};
  if(window.innerWidth<720){layout.margin={l:49,r:9,t:35,b:53};layout.font={...layout.font,size:10};layout.height=(name==='inventory'||name==='alignment')?380:290;layout.legend={orientation:'h',y:1.16,x:0,font:{size:10}}}
  plotPromises.push(Plotly.newPlot(id,figure.data,layout,config));
}
document.getElementById('audit-json').textContent=JSON.stringify(DATA.audit,null,2);
const number=(v,d=3)=>typeof v==='number'&&Number.isFinite(v)?v.toLocaleString('en-US',{minimumFractionDigits:d,maximumFractionDigits:d}):'Unavailable';
function renderFields(id,pairs){
  const target=document.getElementById(id);if(!target)return;target.replaceChildren();
  for(const [label,value] of pairs){const item=document.createElement('div');item.className='record-item';const span=document.createElement('span');span.textContent=label;const strong=document.createElement('strong');strong.textContent=value===null||value===undefined?'Unavailable':String(value);item.append(span,strong);target.appendChild(item)}
}
function tableElement(rows,fields,limit=100){
  const wrapper=document.createElement('div');wrapper.className='table-scroll bounded-table';
  const table=document.createElement('table'),head=document.createElement('thead'),heading=document.createElement('tr');
  for(const [key,label] of fields){const th=document.createElement('th');th.textContent=label;heading.append(th)}head.append(heading);table.append(head);
  const body=document.createElement('tbody');
  for(const row of rows.slice(0,limit)){const tr=document.createElement('tr');for(const [key,label] of fields){const td=document.createElement('td'),v=row[key];td.textContent=v===null||v===undefined?'Unavailable':typeof v==='number'?number(v,Number.isInteger(v)?0:4):String(v);tr.append(td)}body.append(tr)}table.append(body);wrapper.append(table);
  if(rows.length>limit){const p=document.createElement('p');p.className='caption';p.textContent='Showing '+limit+' of '+rows.length+' records. Complete data remain in the build output.';wrapper.append(p)}
  if(!rows.length){const p=document.createElement('p');p.className='caption';p.textContent='No supplied records in this selection.';wrapper.append(p)}return wrapper;
}
function setSource(id,url){const source=document.getElementById(id);if(!source)return;const safe=typeof url==='string'&&/^https?:\/\//.test(url);source.hidden=!safe;if(safe)source.href=url}
function setWindow(start,end){for(const id of ['inventory-chart','alignment-chart'])if(document.getElementById(id)){const update={'xaxis.autorange':!start,'xaxis2.autorange':!start};if(start){update['xaxis.range']=[start,end];update['xaxis2.range']=[start,end]}Plotly.relayout(id,update)}}
for(const button of document.querySelectorAll('[data-range]'))button.addEventListener('click',()=>{
  const range=button.dataset.range;for(const b of document.querySelectorAll('[data-range]')){b.classList.toggle('selected',b===button);b.setAttribute('aria-pressed',String(b===button))}
  if(range==='All'){setWindow(null,null);return}const xs=DATA.plots.inventory.data[0].x||[],last=xs.at(-1);if(!last)return;const end=new Date(last),start=new Date(last);start.setFullYear(start.getFullYear()-(range==='1Y'?1:5));setWindow(start.toISOString().slice(0,10),end.toISOString().slice(0,10));
});
const inventorySelect=document.getElementById('inventory-record');
if(inventorySelect&&(DATA.inventory||[]).length){
  DATA.inventory.forEach((row,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=row.release_date+' / week '+row.week_ending;inventorySelect.appendChild(option)});inventorySelect.value=String(DATA.inventory.length-1);
  function showInventory(){const row=DATA.inventory[Number(inventorySelect.value)];if(!row)return;const quote=(DATA.observations||[]).find(q=>q.decision_date===row.release_date)||{};renderFields('inventory-detail',[
    ['Observation week',row.week_ending],['Actual release',row.release_date],['Knowledge cutoff',row.knowledge_cutoff],['Inventory (million bbl)',number(row.cushing_mbbl)],['Seasonal z',number(row.inv_z)],['State',row.state],['One-week change (million bbl)',number(row.inv_delta1)],['Four-week change (million bbl)',number(row.inv_delta4)],['Prior public quote',quote.price_date],['F2-F3 ($/bbl)',number(quote.spread)],['Original report ID',row.report_id],['Source SHA-256',row.sha256]]);setSource('inventory-source',row.source_url)}
  inventorySelect.addEventListener('change',showInventory);showInventory();
}
let selectedCase=0;
function showCaseEvidence(){const c=DATA.cases[selectedCase]||{},rows=c.evidence||[],select=document.getElementById('case-record'),row=rows[Number(select?.value)]||{};if(!rows.length){renderFields('case-evidence',[['Selected decision',c.decision_date],['Actual contract pair',(c.near_contract||'')+' / '+(c.far_contract||'')],['Forecast ($/bbl)',number(c.prediction,4)],['Actual target ($/bbl)',number(c.actual,4)]]);setSource('case-record-source',null);return}renderFields('case-evidence',[
  ['Observation week',row.week_ending],['Actual release',row.release_date],['Knowledge cutoff',row.knowledge_cutoff],['Inventory (million bbl)',number(row.cushing_mbbl)],['Seasonal z',number(row.inv_z)],['State',row.state],['Four-week change (million bbl)',number(row.inv_delta4)],['Preceding quote date',row.price_date],['F2-F3 ($/bbl)',number(row.spread)],['Quote identity',row.quote_identity],['Source SHA-256',row.sha256]]);setSource('case-record-source',row.source_url)}
function showCase(index,changeWindow=true){
  const c=DATA.cases[index];if(!c)return;selectedCase=index;
  for(const b of document.querySelectorAll('[data-case]')){b.classList.toggle('selected',Number(b.dataset.case)===index);b.setAttribute('aria-pressed',String(Number(b.dataset.case)===index))}
  document.getElementById('case-title').textContent=c.title||'Historical case';document.getElementById('case-summary').textContent=c.selection||c.summary||'No selection record supplied.';setSource('case-source',c.source_url);
  const narrative=document.getElementById('case-narrative');narrative.replaceChildren();
  for(const [key,label] of [['known_then','What was known'],['model_judgment','Model evidence and its status'],['subsequently_observed','What was observed later'],['interpretation','Interpretation and competing explanations'],['failed_hypothesis','What failed'],['next_check','Next information to obtain'],['falsifier','What would weaken the view'],['commercial_impact','Commercial consequence']]){
    if(!c[key])continue;const section=document.createElement('article'),h=document.createElement('h3'),p=document.createElement('p');h.textContent=label;p.textContent=c[key];section.append(h,p);narrative.appendChild(section);
  }
  document.getElementById('case-anchors').replaceChildren(tableElement(c.anchors||[],[['anchor_label','Why this record'],['release_date','Release'],['week_ending','Week'],['cushing_mbbl','Stock, million bbl'],['inv_z','z'],['price_date','Quote date'],['spread','F2-F3, $/bbl']]));
  const select=document.getElementById('case-record');select.replaceChildren();(c.evidence||[]).forEach((row,i)=>{const option=document.createElement('option');option.value=String(i);option.textContent=row.release_date+' / week '+row.week_ending;select.appendChild(option)});showCaseEvidence();
  const actual=document.getElementById('case-actuals');actual.replaceChildren();
  if((c.actual_predictions||[]).length){const h=document.createElement('h3');h.textContent='Supplied actual-contract forecasts and same-pair outcomes';actual.append(h,tableElement(c.actual_predictions.filter(p=>['B1','B3'].includes(p.model)),[['decision_date','Decision'],['model','Model'],['near_contract','Near'],['far_contract','Far'],['entry_date','Entry'],['exit_date','Exit'],['prediction','Forecast $/bbl'],['y','Actual $/bbl']]));}
  if((c.actual_intervals||[]).length){const h=document.createElement('h3');h.textContent='B3 baseline positions and interval accounting';actual.append(h,tableElement(c.actual_intervals,[['event_id','Event'],['quantity','Spread units'],['gross_pnl','Gross USD'],['fees','Fees USD'],['slippage','Slippage USD'],['net_pnl','Net USD']]));}
  if((c.contract_contributions||[]).length){const h=document.createElement('h3');h.textContent='Each leg contribution';actual.append(h,tableElement(c.contract_contributions,[['event_id','Event'],['contract_id','Actual contract'],['leg','Leg'],['gross_pnl','Gross USD'],['fees','Fees USD'],['slippage','Slippage USD'],['net_pnl','Net USD']]));}
  if(changeWindow&&c.start&&c.end)setWindow(c.start,c.end);
}
for(const button of document.querySelectorAll('[data-case]'))button.addEventListener('click',()=>showCase(Number(button.dataset.case)));
const caseRecord=document.getElementById('case-record');if(caseRecord)caseRecord.addEventListener('change',showCaseEvidence);
if(DATA.cases.length)showCase(0,false);
const clear=document.getElementById('clear-case');if(clear)clear.addEventListener('click',()=>{setWindow(null,null);for(const b of document.querySelectorAll('[data-range]')){const active=b.dataset.range==='All';b.classList.toggle('selected',active);b.setAttribute('aria-pressed',String(active))}});
const predModel=document.getElementById('prediction-model'),predEvent=document.getElementById('prediction-event'),predPartition=document.getElementById('prediction-partition');
if(predModel&&predEvent&&DATA.predictions.length){
  if(predPartition)predPartition.value='test';
  const selectedPartition=()=>predPartition?.value||'test';
  const models=[...new Set(DATA.predictions.map(p=>p.model))];for(const model of models){const option=document.createElement('option');option.value=model;option.textContent=model;predModel.appendChild(option)}if(models.includes('B3'))predModel.value='B3';
  function fillEventChoices(){const previous=predEvent.value;predEvent.replaceChildren();for(const p of DATA.predictions.filter(p=>p.model===predModel.value&&p.partition===selectedPartition())){const option=document.createElement('option');option.value=String(p.event_id??p.decision_date);option.textContent=p.decision_date+' / '+(p.partition||'')+' / '+(p.near_contract||'')+' - '+(p.far_contract||'');predEvent.appendChild(option)}if([...predEvent.options].some(o=>o.value===previous))predEvent.value=previous;showEvent()}
  function showEvent(){const p=DATA.predictions.find(p=>p.model===predModel.value&&p.partition===selectedPartition()&&String(p.event_id??p.decision_date)===predEvent.value);if(!p){renderFields('prediction-detail',[['Evidence period',selectedPartition()],['Availability','No supplied events for this model and period']]);return}renderFields('prediction-detail',[['Decision cutoff',p.cutoff||p.decision_date],['Known price date',p.known_price_date],['Entry / exit',(p.entry_date||'')+' / '+(p.exit_date||'')],['Fixed contract pair',(p.near_contract||'')+' / '+(p.far_contract||'')],['Prediction ($/bbl)',number(p.prediction)],['Observed target ($/bbl)',number(p.y)],['Inventory z-score',number(p.inv_z)],['Partition',p.partition],['Evaluation role',p.evaluation_role],['Evidence meaning',p.partition==='validation'?'Development-selected reconstruction; not independent final evidence':p.partition==='recent'?'Separate historical update; not a live trading record':'Locked primary test']])}
  predModel.addEventListener('change',fillEventChoices);predEvent.addEventListener('change',showEvent);if(predPartition)predPartition.addEventListener('change',fillEventChoices);fillEventChoices();
  Promise.all(plotPromises).then(()=>{const chart=document.getElementById('predictions-chart');if(chart&&chart.on)chart.on('plotly_click',ev=>{const date=String(ev.points?.[0]?.x||'').slice(0,10),p=DATA.predictions.find(r=>r.model===predModel.value&&r.partition==='test'&&String(r.decision_date).slice(0,10)===date);if(p){if(predPartition)predPartition.value='test';fillEventChoices();predEvent.value=String(p.event_id??p.decision_date);showEvent()}})});
}
const ledgerSelect=document.getElementById('ledger-variant'),ledgerDay=document.getElementById('ledger-day');
function showLedgerDay(){
  const l=DATA.ledgers[Number(ledgerSelect.value)]||DATA.ledgers[0];if(!l)return;
  const rows=(l.contract_daily||[]).filter(r=>r.trade_date===ledgerDay.value),day=(l.daily||[]).find(r=>r.trade_date===ledgerDay.value),target=document.getElementById('ledger-contract-table');
  const confirmedFlat=day&&day.contracts_traded===0&&day.positions&&typeof day.positions==='object'&&!Array.isArray(day.positions)&&Object.keys(day.positions).length===0;
  if(!rows.length&&confirmedFlat){const p=document.createElement('p');p.className='caption';p.textContent='Flat; no contracts held or traded this day';target.replaceChildren(p);return}
  target.replaceChildren(tableElement(rows,[['contract_id','Actual contract'],['start_position','Start position'],['previous_settlement','Prior settle'],['current_settlement','Settle'],['gross_pnl','Gross USD'],['quantity_change','Net trade'],['contracts_traded','Turnover'],['slippage','Slippage USD'],['fees','Fees USD'],['net_contribution','Net USD'],['end_position','End position']]));
}
if(ledgerSelect&&DATA.ledgers.length){
  for(const [i,l] of DATA.ledgers.entries()){const option=document.createElement('option');option.value=String(i);option.textContent=l.scenario_name||[l.partition,l.model,l.scenario||'base',l.cost_ticks+' ticks',l.force_roundtrip?'Forced close and reopen':'Net contract changes'].join(' / ');ledgerSelect.appendChild(option)}
  function showLedger(){
    const l=DATA.ledgers[Number(ledgerSelect.value)]||DATA.ledgers[0],s=l.summary||{},daily=l.daily||[];
    renderFields('ledger-summary',[['Scenario',l.scenario_name],['Net P&L ($)',number(s.net_pnl,2)],['Maximum drawdown ($)',number(s.max_drawdown,2)],['Contracts traded',number(s.contracts_traded,0)],['Fees / slippage ($)',number(s.fees,2)+' / '+number(s.slippage,2)],['Break-even roundtrip ($/bbl)',s.contracts_traded===0&&s.break_even_roundtrip_cost_per_bbl===null?'Not defined: zero contract turnover':number(s.break_even_roundtrip_cost_per_bbl,4)],['Economic evidence',s.economic_evidence||s.status],['Variation-margin outflow ($)',number(s.variation_margin_outflow,2)]]);
    const layout={paper_bgcolor:'rgba(0,0,0,0)',plot_bgcolor:'rgba(0,0,0,0)',font:{family:'Arial,sans-serif',size:11,color:'#173444'},height:290,margin:{l:60,r:10,t:20,b:43},xaxis:{showgrid:false},yaxis:{gridcolor:'#dce2df',zerolinecolor:'#9baaa8',title:'US dollars'}};
    Plotly.react('ledger-pnl-chart',[{x:daily.map(r=>r.trade_date),y:daily.map(r=>r.cumulative_pnl),type:'scatter',mode:'lines',line:{color:'#237f78',width:1.7},customdata:daily.map(r=>[r.net_pnl,r.contracts_traded]),hovertemplate:'%{x}<br>Cumulative %{y:.2f} USD<br>Daily net %{customdata[0]:.2f} USD<br>Contracts %{customdata[1]}<extra></extra>'}],layout,config);
    Plotly.react('ledger-dd-chart',[{x:daily.map(r=>r.trade_date),y:daily.map(r=>r.drawdown),type:'scatter',mode:'lines',line:{color:'#bd604a',width:1.4},fill:'tozeroy',fillcolor:'rgba(189,96,74,0.13)',hovertemplate:'%{x}<br>Drawdown %{y:.2f} USD<extra></extra>'}],layout,config);
    if(ledgerDay){const previous=ledgerDay.value;ledgerDay.replaceChildren();daily.forEach(row=>{const option=document.createElement('option');option.value=row.trade_date;option.textContent=row.trade_date;ledgerDay.appendChild(option)});if(daily.some(row=>row.trade_date===previous))ledgerDay.value=previous;showLedgerDay()}
    const dates=document.getElementById('ledger-execution-dates');if(dates){dates.replaceChildren();if((l.execution_dates||[]).length){const h=document.createElement('h3');h.textContent='Original and delayed execution dates';dates.append(h,tableElement(l.execution_dates,[['event_id','Event'],['original_entry_date','Original entry'],['entry_date','Delayed entry'],['original_exit_date','Original exit'],['exit_date','Delayed exit']],30))}}
  }
  ledgerSelect.addEventListener('change',showLedger);if(ledgerDay)ledgerDay.addEventListener('change',showLedgerDay);showLedger();
}
'''


def _primary_comparison(ctx: dict) -> dict:
    comparisons = _records(ctx["research"].get("comparisons"))
    preferred = [r for r in comparisons if r.get("partition") == "test" and r.get("candidate") == "B3" and r.get("baseline") == "B1" and r.get("block_size") == 8]
    return preferred[0] if preferred else {}


def _forecast_sentence(ctx: dict) -> str:
    if ctx["public"]:
        return "Predictive and trading evidence is pending because verified actual-contract market inputs are not yet available. No conclusion about forecast skill follows from this missing test."
    primary = _primary_comparison(ctx)
    if not primary:
        return "Actual-contract research has been supplied. Inspect the model comparisons and robustness tables; no primary comparison was identified in the report schema."
    low, high = _number(primary.get("ci_low")), _number(primary.get("ci_high"))
    judgment = ("There is insufficient evidence that inventory improves prediction." if low is not None and high is not None and low <= 0 <= high else
                "The interval supports lower B3 error under this fixed research process." if low is not None and low > 0 else
                "The interval favors the market-only baseline." if high is not None and high < 0 else "The comparison interval is unavailable.")
    return (f"The primary B3-versus-B1 comparison reports an MAE improvement of {_fmt(primary.get('mae_improvement'), 4)} $/bbl "
            f"with a block-bootstrap interval of [{_fmt(primary.get('ci_low'), 4)}, {_fmt(primary.get('ci_high'), 4)}] "
            f"across {_fmt(primary.get('n'), 0)} paired observations. Positive improvement means a lower error than B1. "
            f"{judgment} Evidence classification: {str(primary.get('evidence', 'not supplied')).replace('_', ' ')}.")


def _descriptive_sentence(ctx: dict) -> str:
    return (f"The matched descriptive sample contains {_fmt(ctx['n'], 0)} observations from {_date(ctx['start'])} to {_date(ctx['end'])}. "
            f"The Spearman correlation between inventory pressure and the preceding valid public F2-F3 rank spread is {_fmt(ctx['rho'], 3)}. "
            "This links a new inventory publication to an already-existing curve, not to a future holding-period outcome.")


def _md_table(rows: list[dict], fields: list[tuple[str, str]]) -> str:
    def cell(row: dict, key: str) -> str:
        value=row.get(key)
        return (_pvalue(value) if key == "p_value" else _fmt(value,4) if isinstance(value,float) else _plain(value)).replace("|","/").replace("\n"," ")
    return ("| "+" | ".join(label for _,label in fields)+" |\n|"+"|".join("---" for _ in fields)+"|\n"
            +"\n".join("| "+" | ".join(cell(row,key) for key,_ in fields)+" |" for row in rows))


def _memo(ctx: dict) -> str:
    r,m=ctx["result"],ctx["mechanism"]
    status="Public mechanism research; actual-contract forecasting and trading evidence remain untested." if ctx["public"] else "Actual-contract historical research; performance remains simulated."
    parts=[f"# {TITLE}\n\n## {SUBTITLE}\n\n**Author:** {r.get('author','Independent researcher')}  \n**Evidence status:** {status}  \n**Inventory cutoff:** {_date(r.get('data_cutoff'))}  \n**Result version:** {r.get('run_id')}  \n**Generated:** {r.get('generated_at')}",
           "## 1. Research answer\n\n### "+ctx["headline"],ctx["conclusion"],
           "**Question.** "+(PUBLIC_QUESTION if ctx["public"] else "In addition to the current curve, its recent change and volatility, does a published Cushing inventory reading improve the forecast of the next fixed-pair WTI M2-M3 spread change?"),
           _descriptive_sentence(ctx),_mechanism_sentence(ctx),_forecast_sentence(ctx),
           "![Public inventory-curve relationship](figures/descriptive_relationship.png)",
           "## 2. Define the physical state and information set",INVENTORY_DEFINITION,
           "The public spread is **F2 minus F3**, in USD per barrel. A positive value means the second listed delivery month is dearer than the third; a negative value means the third is dearer. Rank labels change with contract succession. Their difference is useful for a market-state description, but a rank change is not a fixed-contract investment return.",
           "**Chronology:** the observation week records when stocks were measured. The actual release date controls when they become known. The descriptive study pairs each release to its last valid preceding public quote. The quote therefore predates the new stock information; this is neither a release-response test nor a forward-return test.",
           "**Physical mechanism:** deliverable barrels, accessible receiving space and flow capacity influence the relative value of earlier delivery. Aggregate stock does not reveal lease commitments, operational access, injection rates or forthcoming replenishment. A statistical state calls for a specific physical check; it does not identify an executable storage trade.",
           _md_table(ctx["regimes"],[("state","State"),("n","Releases"),("median_spread","Median $/bbl"),("q25","25th percentile"),("q75","75th percentile")]),
           "The whiskers are interquartile ranges of observed spreads, not confidence intervals. An ordered set of medians could also arise from a simple linear relationship.",
           "## 3. Exploratory shape and stability",EXPLORATION_DISCLOSURE,
           "**D1** uses inventory z, year intercepts and seasonal sine/cosine. **D2** adds fixed bends max(0, -1-z) and max(0, z-1). Both use the same valid observations. The joint HAC test asks whether those two additional coefficients are zero. Fitted levels and fit errors are in-sample descriptions, not estimates of future trading performance.",
           _mechanism_sentence(ctx),_sensitivity_sentence(ctx)]
    if m:
        parts += ["![Controlled relationship and slope uncertainty](figures/controlled_relationship.png)",
                  _md_table(_slope_rows(ctx),[("state","D2 state"),("slope","Slope $/bbl per z"),("ci_low","95% lower"),("ci_high","95% upper"),("n","Releases"),("years","Years"),("status","Support")]),
                  "The primary slope intervals use 2,000 resamples in moving blocks of eight publication cycles; the same resampling indices apply to every model. Block lengths four and thirteen are retained as sensitivities. A slope interval and the joint test address different hypotheses. Missing intervals are not replaced by zero.",
                  "### In-sample fit only",
                  _md_table([{"model":name,"r_squared":row.get("in_sample_r_squared"),"mae":row.get("in_sample_mae"),"rmse":row.get("in_sample_rmse")} for name,row in m.get("models",{}).items()],[("model","Specification"),("r_squared","R squared"),("mae","In-sample MAE"),("rmse","In-sample RMSE")]),
                  "### Every year-deletion diagnostic",
                  _md_table([{"excluded_year":row.get("excluded_year"),"n":row.get("n"),"p_value":(row.get("main_test") or {}).get("p_value"),"status":(row.get("main_test") or {}).get("status")} for row in _records(m.get("leave_one_year_out"))],[("excluded_year","Excluded year"),("n","Remaining releases"),("p_value","Joint-test p"),("status","Support")])]
    parts += ["![Within-year association](figures/annual_stability.png)",_annual_note(ctx),
              _md_table(ctx["yearly"],[("year","Year"),("n","Releases"),("spearman","Within-year rho")]),
              "**2019 diagnostic:** a weak within-year association challenges the transfer of the pooled pattern to every year. A small positive estimate does not establish a reversed mechanism. This counterexample was selected after inspecting the annual table and is disclosed as exploratory.",
              "**Scale diagnostic:** z measures deviation from a rolling historical reference. Persistent changes in inventory levels and low reference variability can produce large absolute scores. No winsorization or altered knot was introduced to make the fit look better. Check the saved historical reference mean, standard deviation and sample count before giving an extreme z-score an operational interpretation.",
              "## 4. Historical decision cards"]
    for case in ctx["cases"]:
        parts += ["### "+str(case.get("title","Historical case")),"**Selection:** "+str(case.get("selection") or "No selection rule supplied.")]
        for key,label in [("known_then","What was known"),("model_judgment","Model evidence and status"),("subsequently_observed","What was observed later"),("interpretation","Interpretation and alternatives"),("failed_hypothesis","What failed"),("next_check","Next information to obtain"),("falsifier","What would weaken the view"),("commercial_impact","Commercial consequence")]:
            if case.get(key): parts.append("**"+label+":** "+str(case[key]))
        anchors=_records(case.get("anchors"))
        if anchors:
            parts.append(_md_table(anchors,[("anchor_label","Anchor"),("release_date","Release"),("week_ending","Week"),("cushing_mbbl","Stock, million bbl"),("inv_z","z"),("price_date","Quote date"),("spread","F2-F3 $/bbl")]))
            parts.append("Original records: "+"; ".join(f"[release {row.get('release_date')}]({_safe_url(row.get('source_url'))})" for row in anchors if _safe_url(row.get("source_url"))))
        if _safe_url(case.get("source_url")):
            parts.append(f"[Case context source]({_safe_url(case.get('source_url'))}). Later context is not inserted into earlier decision inputs.")
        if case.get("id")=="failure": parts.append(_failure_sentence(ctx))
        if _records(case.get("contract_contributions")):
            parts.append(_md_table(case["contract_contributions"],[("event_id","Event"),("contract_id","Actual contract"),("leg","Leg"),("gross_pnl","Gross USD"),("fees","Fees USD"),("slippage","Slippage USD"),("net_pnl","Net USD")]))
    if not ctx["public"]:
        metrics=_records(ctx["research"].get("metrics"))
        parts += ["## 5. Locked forecasts and economic value",_forecast_sentence(ctx),
                  _md_table([row for row in _records(ctx["research"].get("comparisons")) if row.get("partition")=="test"],COMPARISON_FIELDS),
                  "### Primary test: four models on one sample",
                  _md_table([row for row in metrics if row.get("partition")=="test" and row.get("scope")=="overall" and row.get("model") in {"B0","B1","B2","B3"}],MAIN_METRIC_FIELDS),
                  "### Test-year and inventory-state diagnostics",
                  _md_table([row for row in metrics if row.get("partition")=="test" and row.get("scope") in {"year","state"}],METRIC_FIELDS),
                  "### Recent historical update: separate from the primary result",
                  _md_table([row for row in metrics if row.get("partition")=="recent" and row.get("scope")=="overall"],METRIC_FIELDS),
                  _md_table([row for row in _records(ctx["research"].get("comparisons")) if row.get("partition")=="recent"],COMPARISON_FIELDS),
                  "### Development-selected reconstruction",
                  "The 2019-2021 validation predictions use penalties chosen with those development years. They are not independent final evidence and must not be presented as a locked forecast available in 2020.",
                  _md_table([row for row in metrics if row.get("partition")=="validation" and row.get("scope")=="overall"],METRIC_FIELDS),
                  "### Cost and execution scenarios",
                  _md_table([{"scenario_name":_scenario_label(l),"break_even_label":_break_even(l.get("summary",{})),**l.get("summary",{})} for l in _records(r.get("ledgers"))],[("scenario_name","Scenario"),("net_pnl","Net USD"),("max_drawdown","Max drawdown USD"),("contracts_traded","Contracts traded"),("break_even_label","Break-even roundtrip $/bbl"),("economic_evidence","Evidence")]),
                  "Baseline cost assumes one adverse tick per leg per side and $2.50 fees per contract per side: $0.05/bbl for a complete two-leg roundtrip. The signal threshold is fixed at 1.5 times that cost, or $0.075/bbl. Cost stresses keep signals fixed. Contract-day marks and fills reconcile to portfolio days and interval legs; positions finish flat with costs charged.",
                  "No historical margin series or capital allocation is available, so no capital return is reported. Negative portfolio variation margin measures cumulative settlement cash outflows; it is not a peak liquidity or initial margin requirement.",
                  "### Retained robustness results"]
        robustness=_robustness_views(ctx)
        parts.append(_md_table(robustness["flat"],[("check","Check"),*COMPARISON_FIELDS]))
        for section in robustness["nested"]:
            parts += ["#### "+section["name"]+": "+str(section["status"]),section["coverage"]+"; validation coverage: "+str(section["validation_status"]),
                      _md_table(section["comparisons"],COMPARISON_FIELDS),_md_table([row for row in section["metrics"] if row.get("scope")=="overall"],METRIC_FIELDS)]
        for section in robustness["executions"]:
            parts += ["#### Extra execution-day delay: "+str(section["status"]),str(section["forecast_policy"] or "No forecast policy supplied"),
                      _md_table([section],[("common_events","Common test events"),("net_pnl","Net USD"),("max_drawdown","Max drawdown USD"),("contracts_traded","Contracts traded")]),
                      _md_table(section["execution_dates"],[("event_id","Event"),("original_entry_date","Original entry"),("entry_date","Delayed entry"),("original_exit_date","Original exit"),("exit_date","Delayed exit")])]
    parts += ["## Methods, limits and reproduction",
              "The PDF, HTML, editable memo and interview notes read one result object. The webpage selects precomputed records and scenarios; it does not recompute a financial result. A time filter changes the exploration window, not the formal sample or conclusion.",
              "Source hashes establish local snapshot consistency. Publication-period reconstruction does not prove that every archived official file has remained unchanged since the first release. Missing actual-contract data and unavailable support remain explicit, rather than becoming zero errors or zero returns.",
              "**Commands:** validate the snapshot; build the research outputs; report from those saved results. Follow the project README and frozen configuration. Actual-contract input specifications, source audits and the protocol remain in the local package.",
              "\n".join("- "+str(limit) for limit in r.get("limitations",[])),
              "### Sources", "\n".join(f"- [{src.get('title','Source')}]({_safe_url(src.get('url'))}) - {src.get('role','')}" for src in ctx["sources"] if _safe_url(src.get("url"))),
              "Independent portfolio research. No affiliation with Glencore or an exchange is implied. Source terms govern redistribution."]
    return "\n\n".join(parts)+"\n"


def _interview(ctx: dict) -> str:
    r=ctx["result"]
    m=ctx["mechanism"]
    main_p=(m.get("main_test") or {}).get("p_value")
    shape_zh=("无法运行指定的形状检验" if _number(main_p) is None else
              "当前样本不足以充分确认额外弯折" if main_p >= .05 else
              "当前规格发现额外形状，但必须结合控制变量和删年份敏感性解释")
    year2019=next((row for row in ctx["yearly"] if str(row.get("year"))=="2019"),{})
    case2020=_case_by_id(ctx,"2020")
    anchors=_records(case2020.get("anchors"))
    focus=anchors[min(1,len(anchors)-1)] if anchors else {}
    build=_number(focus.get("inv_delta4"))
    case_line=(f"In April 2020, stocks still received a {focus.get('state','Normal')} seasonal label after rising {_fmt(build,2)} million barrels in four weeks. "
               if build is not None else "The 2020 case asks whether a normal seasonal label can miss urgent receiving-capacity pressure. ")
    if ctx["public"]:
        intro=("I studied how Cushing inventory relative to its seasonal history relates to the WTI curve, and where that relationship breaks down. "
               f"I reconstructed publication-dated stock records and matched {_fmt(ctx['n'],0)} releases to the preceding public curve quote. "
               f"The pooled correlation was {_fmt(ctx['rho'],3)}, and I checked how that relationship varied across years. "
               f"The exploratory joint shape test, after year and seasonal controls, has a p-value of {_pvalue(main_p)}. "
               +case_line+"That is why I would check lease commitments, injection capacity and expected flows alongside the stock level. "
               "I also implemented and tested the fixed-contract forecasting and daily accounting workflow. Those predictive and trading tests await verified month-contract data. "
               "The useful result is a better physical-market question, with a clear distinction between explaining a curve and forecasting its next move.")
        bullets=[
            f"Reconstructed publication-dated Cushing inventories and analyzed {_fmt(ctx['n'],0)} WTI rank-spread observations; tested controlled nonlinear relationships with HAC inference, paired block resampling and year-deletion diagnostics, retaining unstable and adverse findings.",
            "Implemented and unit-tested a fixed-contract WTI forecasting and accounting workflow with publication cutoffs, market-only benchmarks, net contract turnover and reconciled daily leg attribution; real-contract forecast and trading validation remain pending."]
    else:
        primary=_primary_comparison(ctx)
        intro=("I studied whether Cushing inventory improves WTI calendar-spread forecasts beyond current market information. "
               "I use a publication-aware cutoff, a fixed actual-contract M2-M3 pair and a market-only benchmark. "
               f"Across {_fmt(primary.get('n'),0)} paired test decisions, adding inventory to the market-only model produces an MAE improvement of {_fmt(primary.get('mae_improvement'),4)} dollars per barrel, "
               f"with a 95 percent interval from {_fmt(primary.get('ci_low'),4)} to {_fmt(primary.get('ci_high'),4)}. "
               "I interpret that evidence separately from the daily contract ledger, including two-leg costs and the largest forecast failure. "
               +case_line+"That example explains why the model includes inventory changes as well as levels. "
               "The commercial follow-up is to check storage access and expected flows before assigning the inventory signal a trading value.")
        bullets=[f"Evaluated Cushing inventory information against market-only WTI spread forecasts using actual-contract fixed pairs and {_fmt(primary.get('n'),0)} paired test decisions, with locked model specifications, time-ordered validation and block-bootstrap uncertainty.",
                 "Built reproducible futures accounting with net contract turnover, daily leg-level marks, terminal costs and interval reconciliation; retained execution sensitivities and mechanically selected forecast failures for commercial interpretation."]
    case_chinese=(f"2020年4月15日报告的季节状态仍是 {focus.get('state','Normal')}，z={_fmt(focus.get('inv_z'),2)}，但四周库存已经增加 {_fmt(build,3)} 百万桶。" if build is not None else "2020案例提醒我们：历史季节状态与储运的运营紧急程度可能不同。")
    return f'''# Cushing 库存与 WTI 月差：理解与面试讲述

**状态：{'公开研究已完成；预测与交易测试尚未完成' if ctx['public'] else '实际合约历史研究；不是实盘业绩'}**  
**数据截止：{_date(r.get('data_cutoff'))}**  
**结果版本：{r.get('run_id')}**

## 60 秒英文介绍

{intro}

## 三分钟讲述顺序

1. **0:00–0:25：为什么交易台关心。** 同样一桶原油，不同交付时间有不同价值。库欣库存与收货、储存、运输约束可能改变这个相对价值。问题是：库存是否增加了现有曲线以外的信息？
2. **0:25–1:00：先解释数据究竟说明什么。** 展示“观察周—实际公布日—报价日”。本次公开图把新公布库存与之前已经存在的 F2-F3 报价配对；不能称为公布后的交易效果。
3. **1:00–1:40：说真实结果与反例。** {_fmt(ctx['n'],0)}次匹配发布，整体相关 {_fmt(ctx['rho'],3)}；2019年只有 {_fmt(year2019.get('spearman'),3)}，接近没有关联。控制年份与季节后，形状检验 p={_pvalue(main_p)}。非显著不等于已证明关系是线性的；删年份与去除年份控制会改变结果。
4. **1:40–2:20：用一个运营例子解释模型设计。** {case_chinese} 这说明为什么 B2 不只使用库存水平，还保留一周和四周变化。它是研究设计动机，{'尚不是 B2 有预测优势的证据。' if ctx['public'] else '预测价值仍以实际 B1/B2/B3 比较为准。'}
5. **2:20–3:00：商业判断与下一步。** 先查可使用的仓储、注入能力和进出流量，区分持续短缺与预计补库。最后明确{'真实合约数据到位后才检验预测与账本。' if ctx['public'] else '主比较的区间、实际成本敏感性与最大失败，不能只展示盈利区间。'}

## 中文机制解释

**先从两张交货单想起。** 一张是较早交货，另一张是较晚交货。月差等于“较早价格减较晚价格”。正月差说明市场给较早交付更高价格；负月差说明较晚交付更贵。公开图里的 F2/F3 是顺位标签，真正持有一笔价差时必须记住两张具体合约，直到退出都按原合约结算。

**库存水平不是全部。** 库存低可能反映当期可交付原油紧张，但若市场已经预计管道来油恢复，月差也可能先回落。库存还不算特别高，却快速增加，也可能让收货安排先变得紧张。因此需要同时区分：有多少桶、变化多快、哪些桶或空间能用、未来流量会怎样。

**z-score 是历史比较尺。** 用当期库存减去前三年同季节均值，再除以同季节标准差。参考区间为年内位置前后28天，至少20个当时已知观察；z低于−1为Low、高于+1为High。它既不是“储罐剩余空间”，也不是安全运营底线。历史库存制度变化、参考样本波动小，会使绝对z很大。

## 八个核心追问

### 1. 为什么选 Cushing，而不是全国库存？

WTI的交割地与库欣相连，地理位置和收发能力让当地库存具有商业意义。全国库存变化可能反映共同供需因素，却不能说明库欣能收到或交出多少油。项目原计划把“全国商业库存减库欣”作为辅助控制；全国历史lease-stock口径在2016年变化，因此此控制暂不作为已经通过验收的结论。

### 2. 为什么主研究选择 M2–M3？

它靠近原油实物时间价值，同时减少最临近交割月的特殊挤压对常规检验的影响。代价是可能错过最强的M1交割信号，也不能完全消除流动性或交割风险。S=F(M2)−F(M3)：预期S上升，才对应买近卖远。2020年的M1负价事件不能直接充当M2–M3策略盈利证据。

### 3. 库存和月差相关，为什么还要市场信息基准？

市场在报告公布前可能已经通过流量、装运和其他信息形成预期。相关性可能说明曲线已经反映库存状态，而不是库存报告还剩下可交易信息。B1使用当前月差、近期同合约变化、波动、到期距离和季节项；B3只是在这个基准上增加库存信息。只有相同测试样本上的误差比较才回答“有没有额外信息”。

### 4. 如何证明没用到未来？

决策截止是EIA实际发布日期纽约时间23:59:59，不能把观察周末当公布日。结算可用性按固定规则处理；无法确认当天可用则再滞后。下一有效结算才模拟执行，未来目标始终跟踪原合约。标准化只拟合训练集，训练只使用已完成标签，拟合前保留五个交易日间隔。系统还应通过改变未来数据不改变过去特征的反例测试，而不是只口头保证。

### 5. 预测改善为什么仍可能亏损？

MAE变小可能只改善小幅变化，或方向优势太小而不够覆盖双腿成本。基准每腿每方向1tick滑点加$2.50费用，完整两腿往返共$50，即$0.05/桶；开仓门槛固定为$0.075/桶。真实净成交量决定收费：续持不重复收费，反向每腿两手，换月共同腿也可能变动两手。每日结算现金流、回撤和最终平仓都要记清楚。{'目前真实预测与经济价值都未验收，缺数据不是无效结果。' if ctx['public'] else _forecast_sentence(ctx)}

### 6. 哪项结果最反驳最初直觉？

不是把“低库存、月差高”画出来就证明了非线性。控制年份和季节后联合检验 p={_pvalue(main_p)}，{shape_zh}。{_sensitivity_sentence(ctx)} 2019年相关只有 {_fmt(year2019.get('spearman'),3)}，属于弱关系，不足以称为可靠反转。新增分析是在看过汇总后提出的探索，不能包装成事前登记或未见样本发现。

### 7. 哪个实物风险是公开库存看不见的？

{case_chinese} 应追问空位是否已签租约、注入流速是否够、来油是否已排期、能否满足交割责任。统计标签不能回答这些问题。2023案例则提醒：库存低点和月差高点不必同日；预期补库可能先影响曲线，但当前公开数据不能确认究竟是哪项流量因素主导。

### 8. 下一份数据为什么值得拿？

先拿实际月份合约的结算价、最后交易日、有效结算日与行情可用规则：它们解除的是“不能构造可信未来目标和持仓收益”的限制。再拿带日期的管道流量、检修、仓储承诺和现货升贴水：它们帮助区别持续可交付短缺与暂时低库存，以及解释模型失败。两类数据解决不同问题，不能用更多库存指标替代缺失的合约身份。

## 两条英文简历草稿

- {bullets[0]}
- {bullets[1]}

## 面试前自测

- 用自己的话解释：为什么 Normal 的库存状态仍可能让交易台担心？
- 指出某次报告的观察周、公布日、匹配报价日；说明哪项当时还不知道。
- 手算：近腿涨$0.10、远腿涨$0.04，一手买近卖远毛盈亏是+$100−$40=+$60；再说明两腿成本如何扣除。
- 解释 p={_pvalue(main_p)} 的正确含义：{shape_zh}；它不能单独证明经济机制，也不能证明线性一定正确。

所有数字来自当前结果对象。{'可以说“我实现并测试了预测框架”；不可说“我验证了真实预测优势”或“策略赚了钱”。' if ctx['public'] else '结果是历史研究与假设执行，不能称为实盘业绩。'}
'''


def _pdf_text(value: Any) -> str:
    text = str(value if value is not None else "Unavailable")
    for a, b in [("→", " to "), ("−", "-"), ("–", "-"), ("—", "-"), ("’", "'"), ("“", '"'), ("”", '"'), ("≠", "is not"), ("≤", "<="), ("≥", ">="), ("²", "2")]:
        text = text.replace(a, b)
    return text


def _pdf_report(ctx: dict, figures: dict[str, Path], target: Path) -> None:
    width, height, margin = 595.276, 841.89, 43.0
    content_w = width - 2 * margin
    cv = canvas.Canvas(str(target), pagesize=(width, height), pageCompression=1)
    cv.setTitle(f"{TITLE}: {SUBTITLE}")
    cv.setAuthor(str(ctx["result"].get("author", "Independent researcher")))
    cv.setSubject("Public inventory evidence and WTI calendar-spread research")
    def color(v: str) -> Any:
        return colors.HexColor(v)
    def para(text: str, y: float, size: float = 9.3, leading: float | None = None,
             x: float = margin, w: float = content_w, max_h: float | None = None,
             font: str = "Helvetica", ink: str = NAVY) -> float:
        text = html.escape(_pdf_text(text)).replace("\n", "<br/>")
        actual_size = size
        while True:
            style = ParagraphStyle("report", fontName=font, fontSize=actual_size,
                                   leading=leading or actual_size * 1.48, textColor=color(ink),
                                   spaceAfter=0, alignment=TA_LEFT)
            p = Paragraph(text, style)
            _, h = p.wrap(w, 1000)
            if max_h is None or h <= max_h or actual_size <= 7:
                break
            actual_size -= .25
        if max_h is not None and h > max_h:
            raise ValueError(f"PDF text exceeds allotted space: {text[:90]}")
        if y-h < 44:
            raise ValueError(f"PDF text would cross the footer: {text[:90]}")
        p.drawOn(cv, x, y-h)
        return y-h
    def line(y: float) -> None:
        cv.setStrokeColor(color(LINE)); cv.setLineWidth(.6)
        cv.line(margin, y, width-margin, y)
    def label(text: str, y: float, ink: str = TEAL, x: float = margin) -> None:
        cv.setFillColor(color(ink)); cv.setFont("Helvetica-Bold", 8)
        cv.drawString(x, y, _pdf_text(text))
    def image(name: str, y: float, h: float) -> None:
        cv.drawImage(str(figures[name]), margin, y-h, width=content_w, height=h,
                     preserveAspectRatio=True, anchor="c", mask="auto")
    def page(number: int, section: str) -> None:
        cv.setFillColor(color(PAPER)); cv.rect(0, 0, width, height, fill=1, stroke=0)
        cv.setFillColor(color(TEAL)); cv.setFont("Helvetica-Bold", 8)
        cv.drawString(margin, height-31, "INDEPENDENT COMMODITIES RESEARCH")
        cv.setFillColor(color(MUTED)); cv.setFont("Helvetica", 7.5)
        cv.drawRightString(width-margin, height-31, _pdf_text(str(ctx["result"].get("author", "Independent researcher"))))
        line(height-41)
        cv.setFont("Helvetica", 7); cv.setFillColor(color(MUTED))
        cv.drawString(margin, 25, _pdf_text(f"Cushing / {_date(ctx['result'].get('data_cutoff'))} / {ctx['result'].get('run_id','')}"))
        cv.drawRightString(width-margin, 25, f"{section}  |  {number} / 4")
    def title(text: str, y: float, size: float = 26) -> float:
        return para(text, y, size=size, font="Times-Roman", max_h=75, leading=size*1.12)
    def stat(x: float, y: float, name: str, val: str, w: float, value_size: float = 17) -> None:
        para(name.upper(), y, size=7, x=x, w=w, ink=MUTED, max_h=25)
        para(val, y-23, size=value_size, x=x, w=w, font="Helvetica-Bold", max_h=38)
    def note(text: str, y: float, h: float, caution: bool = False) -> None:
        cv.setFillColor(color("#F8EDE5" if caution else "#EBF2EE")); cv.rect(margin, y-h, content_w, h, fill=1, stroke=0)
        cv.setFillColor(color(RUST if caution else TEAL)); cv.rect(margin, y-h, 3, h, fill=1, stroke=0)
        para(text, y-12, size=8.8, x=margin+13, w=content_w-26, max_h=h-24,
             ink="#71533E" if caution else "#3B605A")

    if ctx["public"]:
        page(1, "Research answer")
        label("01 / THE RESEARCH ANSWER", 777)
        y = title(TITLE, 753, 32)
        para(PUBLIC_PDF_QUESTION, y-9, size=9.5, ink=MUTED, max_h=25)
        label("PUBLIC MARKET-STATE EVIDENCE", 678)
        y = para(ctx["headline"], 655, size=19.5, leading=23, font="Times-Roman", max_h=78)
        para(ctx["conclusion"], y-11, size=9.3, max_h=79, ink="#455B64")
        image("descriptive_relationship", 484, 175)
        para("Figure 1. Newly reported seasonal inventory states and the preceding public F2-F3 quote. State medians summarize differences across the historical observations. Whiskers are distributions, not confidence intervals.", 305, size=7.9, ink=MUTED, max_h=40)
        line(255)
        stat(margin, 238, "Matched releases", _fmt(ctx["n"], 0), 145)
        stat(margin+168, 238, "Pooled Spearman rho", _fmt(ctx["rho"], 3), 150)
        year2019 = next((x for x in ctx["yearly"] if str(x.get("year")) == "2019"), {})
        stat(margin+340, 238, "2019: weak-year rho", _fmt(year2019.get("spearman"), 3), 167)
        para(f"Public price overlap: {_date(ctx['start'])} to {_date(ctx['end'])}. Inventory records continue through release {_date(ctx['latest'].get('release_date'))}.", 165, size=8.1, ink=MUTED, max_h=30)
        note("Commercial use: treat inventory as a prompt for checking deliverable barrels and expected flows. A stock level does not establish that a spread will keep strengthening.\n" + _forecast_sentence(ctx), 121, 67, caution=True)
        cv.showPage()

        page(2, "Definition and mechanism")
        label("02 / WHAT DOES INVENTORY STRESS MEAN?", 777)
        title("A statistical state is not a tank limit.", 753)
        para(INVENTORY_DEFINITION, 704, size=9.1, max_h=101)
        controlled = "controlled_relationship" in figures
        image("controlled_relationship" if controlled else "descriptive_relationship", 595, 196)
        para("Figure 2. D1 is linear in z; D2 adds bends at -1 and +1. Year and seasonal controls are held at sample means. Right: D2 slopes and 95% intervals from eight-release-cycle block resampling. Missing intervals indicate inadequate support." if controlled else "Figure 2. State distributions alone cannot establish that a nonlinear model is necessary. The controlled exploration is unavailable in this result.", 395, size=7.9, ink=MUTED, max_h=42)
        label("THE ECONOMIC MECHANISM", 330)
        para("Accessible barrels and the ability to receive, store or move oil affect the relative value of earlier delivery. Low aggregate stock can accompany prompt scarcity; high stock can accompany costly carrying arrangements. Leases, flow limits and expectations can weaken either association.", 317, size=9.1, max_h=71)
        label("WHAT THE SHAPE CHECK ACTUALLY SAYS", 232)
        para(_mechanism_sentence(ctx), 219, size=9.1, max_h=72)
        note(EXPLORATION_DISCLOSURE, 131, 75, caution=True)
        cv.showPage()

        page(3, "Stability and counterexample")
        label("03 / WHERE DOES THE RELATIONSHIP FAIL?", 777)
        title("The pooled pattern is not a standing rule.", 753)
        image("annual_stability", 688, 168)
        para("Figure 3. Within-year correlations show that the pooled association varies across periods. " + _annual_note(ctx), 514, size=7.9, ink=MUTED, max_h=34)
        year2019 = next((x for x in ctx["yearly"] if str(x.get("year")) == "2019"), {})
        note(f"2019 counterexample: n = {_fmt(year2019.get('n'), 0)}, rho = {_fmt(year2019.get('spearman'), 3)}. This is a weak association, not evidence of a reliably reversed relationship. The year was selected after reviewing annual results; it is an exploratory diagnostic.", 459, 82, caution=True)
        label("D2 SLOPES: FITTED SPREAD CHANGE PER Z UNIT", 350)
        y=329
        for row in _slope_rows(ctx):
            text=(f"{row['state']}: {_fmt(row.get('slope'),3)} $/bbl per z; "
                  f"95% block interval [{_fmt(row.get('ci_low'),3)}, {_fmt(row.get('ci_high'),3)}]; "
                  f"n = {_fmt(row.get('n'),0)}, years = {_fmt(row.get('years'),0)}.")
            y=para(text,y,size=8.8,max_h=31)-9
        para(_sensitivity_sentence(ctx), 190, size=8.6, max_h=64)
        note("Evidence boundary: the quote comes before the inventory release. H1 concerns historical market states. H2 needs a fixed-contract future target; H3 separately needs costs and daily cash flows. Neither can be inferred from a pooled correlation or better in-sample fit.", 111, 57, caution=True)
        cv.showPage()

        page(4, "Commercial judgment")
        label("04 / WHAT WOULD A COMMERCIAL ANALYST DO?", 777)
        title("Turn a stock reading into a specific check.", 753)
        y=689
        for case_id, heading, fallback in [("2020", "2020 / RECEIVING CAPACITY", "Inspect storage commitments and injection capacity."),
                                           ("2023", "2023 / REPLENISHMENT EXPECTATIONS", "Separate low current stock from expected future scarcity.")]:
            case = _case_by_id(ctx, case_id)
            label(heading, y)
            if case_id == "2023" and len(_records(case.get("anchors"))) >= 4:
                anchors = case["anchors"]
                peak, trough = anchors[2], anchors[3]
                facts=(f"Highest matched spread: {_fmt(peak.get('spread'),2,True)} $/bbl on quote {peak.get('price_date')}. "
                       f"At the inventory trough released {trough.get('release_date')}, stocks were {_fmt(trough.get('cushing_mbbl'),3)} million bbl and the prior quote was {_fmt(trough.get('spread'),2,True)} $/bbl. Both extrema are hindsight diagnostics.")
            else:
                facts = _case_anchor_text(case, 1)
                anchor = (_records(case.get("anchors")) or [{}])[min(1, len(_records(case.get("anchors")) or [{}])-1)]
                if anchor.get("inv_delta4") is not None:
                    facts += f" Four-week inventory change: {_fmt(anchor.get('inv_delta4'),3,True)} million bbl."
            y=para(facts, y-14, size=8.9, max_h=70)-9
            y=para(str(case.get("commercial_impact") or fallback), y, size=8.9, max_h=57)-9
            y=para("Next check: " + str(case.get("next_check") or fallback), y, size=8.6, ink=MUTED, max_h=57)-20
            line(y+9)
        label("WHAT WOULD CHANGE THE VIEW?", y)
        y=para("2020: verified uncommitted space and injection capacity sufficient for scheduled inflows would weaken a receiving-capacity explanation. 2023: sustained replenishment would weaken persistent scarcity; continued draws and constrained flows would challenge rapid normalization. The current dataset cannot decide between these alternatives.", y-13, size=8.5, max_h=66)-13
        label("NEXT DATA, THEN REPRODUCE", y)
        y=para("Verified month-contract settlements, actual expiries and settlement availability enable the locked B1/B3 forecast comparison. Dated pipeline flows and storage commitments help explain failures. Run validate, build, report using the saved snapshot; hashes verify local consistency, not immutable original publication bytes.", y-13, size=8.1, max_h=54)-12
        y=para("The offline HTML and editable memo retain dated case records, limitations and the full methods. No capital return or real trading performance is claimed.", y, size=7.7, ink=MUTED, max_h=32)-17
        label("PRIMARY SOURCE LINKS",y)
        y-=13
        source_links=[]
        for case_id in ("2020","2023"):
            case=_case_by_id(ctx,case_id)
            anchors=_records(case.get("anchors"))
            if anchors:
                row=anchors[min(1 if case_id=="2020" else 3,len(anchors)-1)]
                source_links.append((f"{case_id}: original inventory report, released {row.get('release_date')}",row.get("source_url")))
        source_links.extend((str(src.get("title","Source")),src.get("url")) for src in _selected_pdf_sources(ctx) if any(word in str(src.get("title","")).lower() for word in ("futures", "2020")))
        for text,url in source_links[:4]:
            new_y=para(text,y,size=7.7,ink=TEAL,max_h=23)
            if _safe_url(url): cv.linkURL(_safe_url(url),(margin,new_y,width-margin,y),relative=0,thickness=0)
            y=new_y-5
        cv.showPage()
    else:
        primary = _primary_comparison(ctx)
        b1 = _select_ledger(ctx, "B1").get("summary", {})
        b3 = _select_ledger(ctx, "B3").get("summary", {})
        failure = _case_by_id(ctx, "failure")
        page(1, "Research answer")
        label("01 / ACTUAL-CONTRACT RESEARCH ANSWER",777)
        title(TITLE,753,32)
        para("Can inventory improve a market-only WTI M2-M3 forecast?",707,size=10.8,ink=MUTED)
        y=para(ctx["headline"],676,size=19.4,leading=23,font="Times-Roman",max_h=74)
        para(ctx["conclusion"],y-12,size=9.3,max_h=71)
        image("forecast_comparison",513,172)
        para("Figure 1. Common-sample forecast MAE for B0, B1, B2 and B3. The primary comparison is B3 against B1 over 2022-2025. Positive improvement means lower B3 error; the paired interval determines the evidence strength.",336,size=7.9,ink=MUTED,max_h=35)
        stat(margin,278,"Paired test observations",_fmt(primary.get("n"),0),155)
        stat(margin+175,278,"MAE improvement $/bbl",_fmt(primary.get("mae_improvement"),4),164)
        stat(margin+350,278,"95% paired interval",f"[{_fmt(primary.get('ci_low'),4)}, {_fmt(primary.get('ci_high'),4)}]",159,value_size=12.8)
        note(_forecast_sentence(ctx),192,92,caution=(_number(primary.get("ci_low")) or 0)<=0)
        para("Historical simulation with actual contracts and stated execution assumptions. Neither a model score nor a positive ledger establishes realized or future trading performance.",82,size=8.1,ink=MUTED,max_h=29)
        cv.showPage()

        page(2,"Information and model")
        label("02 / PHYSICAL MECHANISM AND INFORMATION TIMING",777)
        title("Freeze what was known; keep the same pair.",753)
        para(INVENTORY_DEFINITION,699,size=9.1,max_h=105)
        label("S = F(M2) - F(M3), IN USD PER BARREL",577)
        para("M2/M3 are the second and third unexpired month contracts selected at the information cutoff. A positive spread is backwardation in that curve segment. The selected contract identities remain fixed through the outcome interval; rank changes are not holding profits.",563,size=9.2,max_h=74)
        if ctx.get("mechanism", {}).get("curves") or ctx.get("observations"):
            image("controlled_relationship" if "controlled_relationship" in figures else "descriptive_relationship",474,168)
            para("Figure 2. Public market-state evidence supplies context for the physical mechanism. It does not substitute for the actual-contract forward target; any D1/D2 shape extension is explicitly post-review exploratory work.",299,size=7.9,ink=MUTED,max_h=37)
        else:
            note("Public mechanism evidence is absent from this result. The contract research below can be inspected, but this document does not establish an observed inventory-state relationship.",462,92,caution=True)
            para("Information sequence: published inventory and known settlements -> fixed contract pair -> next valid settlement entry -> the same pair at exit.",344,size=9.3,max_h=52)
        label("CUTOFF TO EXECUTION TO EXIT",244)
        para("Use EIA's actual publication date at 23:59:59 New York time. Execute at the next valid settlement, with the fixed quote-availability rule. Exit at the next report's execution, ten held sessions or five sessions before the near contract's last trade, whichever is first. Same-day releases yield one decision.",230,size=8.9,max_h=77)
        label("LOCKED COMPARISON",135)
        para("B0 predicts zero change. B1 uses market state; B2 adds linear inventory information; B3 adds fixed inventory bends and interaction. Ridge penalties are selected on 2019-2021 walk-forward MAE; 2022-2025 evaluates fixed rules. Annual expanding fits use completed labels with a five-session gap. The 2026 update is separate historical research.",122,size=8.7,max_h=67)
        cv.showPage()

        page(3,"Economic value")
        label("03 / ECONOMIC VALUE AND RISK",777)
        title("Explain every dollar through held contracts.",753)
        image("ledger_comparison",690,205)
        para("Figure 3. B1 and B3 baseline net P&L; lower panel shows B3 drawdown. Old contract positions earn settlement changes before actual net position changes are charged. Terminal closing costs are included.",482,size=7.9,ink=MUTED,max_h=36)
        rows=[("B1 baseline net P&L",_fmt(b1.get("net_pnl"),2,True)+" USD"),
              ("B3 baseline net P&L",_fmt(b3.get("net_pnl"),2,True)+" USD"),
              ("B3 maximum drawdown",_fmt(b3.get("max_drawdown"),2)+" USD"),
              ("B3 break-even roundtrip cost",_break_even(b3)),
              ("B3 actual contracts traded",_fmt(b3.get("contracts_traded"),0)),
              ("B3 variation-margin outflow",_fmt(b3.get("variation_margin_outflow"),2)+" USD")]
        y=428
        for key,value in rows:
            para(key,y,size=8.7,w=292,max_h=18)
            para(value,y,size=8.7,x=margin+305,w=content_w-305,max_h=18,font="Helvetica-Bold")
            y-=25
        label("FIXED SIGNALS; THREE COST SCENARIOS",260)
        y=242
        for ticks,cost in ((1,.05),(2,.09),(4,.17)):
            ledger=_select_ledger(ctx,"B3",ticks).get("summary",{})
            para(f"{ticks} tick(s) per leg/side; roundtrip assumption {cost:.2f} $/bbl",y,size=8.7,w=332,max_h=20)
            para(_fmt(ledger.get("net_pnl"),2,True)+" USD",y,size=8.7,x=margin+350,w=content_w-350,max_h=20,font="Helvetica-Bold")
            y-=25
        note("One spread unit = 1,000 barrels per leg. Signal threshold: |forecast| > 0.075 $/bbl. Fees: $2.50 per contract per side. Costs follow actual contract turnover; same-direction continuation avoids fictitious new fills. Full close/reopen and extra execution-day delay appear separately in the HTML.",152,78)
        para("No historical margin series or capital allocation is supplied: no return on capital is calculated. Few active intervals mean limited economic evidence. Break-even cost is a turnover-based historical diagnostic, not a quoted execution opportunity.",70,size=7.7,ink=MUTED,max_h=24)
        cv.showPage()

        page(4,"Failure and decision")
        label("04 / FAILURE, COMMERCIAL JUDGMENT AND LIMITS",777)
        title("The strongest challenge to the model.",753)
        label("LARGEST B3 TEST FORECAST ERROR",700,RUST)
        y=para(_failure_sentence(ctx),685,size=9.2,max_h=87)-13
        contributions=_records(failure.get("contract_contributions"))
        for leg in contributions:
            y=para(f"{leg.get('leg','Leg').title()} {leg.get('contract_id')}: gross {_fmt(leg.get('gross_pnl'),2,True)}; fees {_fmt(leg.get('fees'),2)}; slippage {_fmt(leg.get('slippage'),2)}; net {_fmt(leg.get('net_pnl'),2,True)} USD.",y,size=8.6,max_h=30)-7
        if not contributions:
            y=para("No leg attribution supplied for this case; review the interval status before a trading interpretation.",y,size=8.6,max_h=30)-10
        y=para(str(failure.get("failed_hypothesis") or "The conditional forecast missed this realized interval; that does not identify a physical cause."),y,size=8.9,max_h=52)-19
        label("DISTINGUISH THE HISTORICAL CASES",y)
        y=para("2020 is a physical-mechanism case in the development period, not a locked out-of-sample prediction. The 2023 case attaches eligible B1/B3 forecasts, same-pair outcomes and actual two-leg costs. The 2019 counterexample challenges the pooled association without substituting for this mechanically selected failure.",y-13,size=8.8,max_h=66)-19
        label("COMMERCIAL FOLLOW-UP",y)
        y=para("After a forecast miss, first inspect the stored curve, inventory state and each contract's daily mark. Then check dated pipeline nominations, maintenance and nearby physical differentials to distinguish persistent scarcity from expected replenishment. Do not attribute an unexplained error to inventory alone.",y-13,size=8.8,max_h=66)-17
        label("LIMITS AND REPRODUCTION",y)
        y=para("Settlement prices are an execution benchmark, not demonstrated executable quotes. Aggregate stock does not measure uncommitted tank space. The national control remains separately governed by its historical-definition audit. Bootstrap intervals condition on the fixed workflow and cannot remove structural change. Rebuild with validate, build, report; every figure shares this result version.",y-13,size=8.3,max_h=77)-14
        for src in _selected_pdf_sources(ctx)[:4]:
            text=str(src.get("title","Source"))
            new_y=para(text,y,size=7.6,max_h=20,ink=TEAL)
            url=_safe_url(src.get("url"))
            if url: cv.linkURL(url,(margin,new_y,width-margin,y),relative=0,thickness=0)
            y=new_y-4
        cv.showPage()

    cv.save()


def render_report(result: dict, output_dir: Path) -> dict[str, str]:
    """Create the self-contained HTML, exact four-page PDF and editable briefings."""
    output_dir=Path(output_dir)
    output_dir.mkdir(parents=True,exist_ok=True)
    ctx=_context(result)
    plots=_plot_data(ctx)
    figures=_png_figures(ctx,output_dir)
    paths={"html":output_dir/"report.html", "pdf":output_dir/"research_memo.pdf",
           "memo":output_dir/"research_memo.md", "interview_notes":output_dir/"interview_notes.md"}
    paths["html"].write_text(_html_report(ctx,plots),encoding="utf-8")
    paths["memo"].write_text(_memo(ctx),encoding="utf-8")
    paths["interview_notes"].write_text(_interview(ctx),encoding="utf-8")
    _pdf_report(ctx,figures,paths["pdf"])
    return {key:str(path.resolve()) for key,path in paths.items()}
