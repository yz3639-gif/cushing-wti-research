"""Presentation helpers for the local options desk."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from enum import Enum
from io import StringIO
import json
from typing import Any

import pandas as pd
import plotly.graph_objects as go

from options_lab.models import VolNode, VolVersion


DESK_CSS = """
<style>
  :root {
    --az-ink: #0b1118;
    --az-panel: #121b25;
    --az-raised: #182330;
    --az-line: #263441;
    --az-text: #edf0f2;
    --az-muted: #99aab9;
    --az-dim: #718597;
    --az-amber: #edba68;
    --az-teal: #6dd8c0;
    --az-red: #f19999;
    --az-sans: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    --az-mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
  }
  html, body, .stApp {
    background: var(--az-ink);
    color: var(--az-text);
    font-family: var(--az-sans);
    color-scheme: dark;
    -webkit-font-smoothing: antialiased;
  }
  .stApp {
    background: radial-gradient(ellipse at 85% 0%, #152230 0, transparent 42%), var(--az-ink);
  }
  [data-testid="stHeader"] { background: transparent; }
  [data-testid="stAppDeployButton"], [data-testid="stDecoration"],
  [data-testid="stStatusWidget"], #MainMenu, footer { display: none !important; }
  [data-testid="stSidebarCollapsedControl"] button,
  [data-testid="stSidebarCollapseButton"] button { color: var(--az-muted); }
  .block-container { max-width: 1536px; padding: 2.3rem 2.6rem 2.2rem; }
  [data-testid="stMainBlockContainer"] { padding-top: 2.3rem; }
  [data-testid="stVerticalBlock"] { gap: 1rem; }
  h1, h2, h3, h4, p, label, [data-testid="stMarkdownContainer"] {
    font-family: var(--az-sans);
  }
  h1, h2, h3, h4 { color: var(--az-text); }
  h1 { font-size: 2.35rem !important; font-weight: 650 !important; letter-spacing: -.055em; }
  h2 { font-size: 1.4rem !important; letter-spacing: -.035em; }
  h3 { font-size: 1.05rem !important; letter-spacing: -.018em; }
  p, li { line-height: 1.6; }
  [data-testid="stMarkdownContainer"] p { font-size: .9rem; }
  [data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {
    color: var(--az-muted); font-size: .77rem; line-height: 1.55;
  }
  a { color: var(--az-amber); text-underline-offset: 3px; }
  a:hover { color: #ffd998; }
  code { color: var(--az-teal); background: #15232d; font-family: var(--az-mono); }
  hr { border-color: var(--az-line); margin: 1.1rem 0; }
  ::selection { background: #edba6840; color: #fff; }

  /* The masthead and the hero establish the desk's own identity. */
  .az-topbar {
    display: flex; align-items: center; gap: 12px; min-height: 48px;
    padding-bottom: 19px; border-bottom: 1px solid var(--az-line); margin-bottom: 9px;
  }
  .az-monogram {
    display: inline-flex; flex: 0 0 38px; align-items: center; justify-content: center;
    width: 38px; height: 38px; border: 1px solid #edba6870; border-radius: 9px;
    color: var(--az-amber); background: #edba680b;
    font: 600 .95rem var(--az-mono); letter-spacing: -.08em;
  }
  .az-wordmark {
    color: var(--az-text); font-size: .77rem; font-weight: 650;
    letter-spacing: .14em; text-transform: uppercase;
  }
  .az-wordmark span {
    display: block; margin-top: 4px; color: var(--az-muted); font-size: .53rem;
    letter-spacing: .12em; font-weight: 400;
  }
  .az-credit { margin-left: auto; color: var(--az-muted); font-size: .73rem; white-space: nowrap; }
  .az-credit strong, .az-credit b { color: #cbd5de; font-weight: 500; }
  .az-hero {
    position: relative; padding: 28px 0 22px;
    display: flex; align-items: flex-start; justify-content: space-between; gap: 24px;
  }
  .az-hero.is-loaded { padding: 12px 0 2px; align-items: center; }
  .az-hero.is-loaded .az-title { font-size: 2.45rem !important; margin-bottom: 4px !important; }
  .az-hero.is-loaded .az-eyebrow { margin-bottom: 6px; }
  .az-hero.is-loaded .az-subtitle { margin-bottom: 0; }
  .az-eyebrow, .desk-kicker {
    color: var(--az-amber); font: 500 .66rem var(--az-mono);
    letter-spacing: .17em; text-transform: uppercase; margin-bottom: 12px;
  }
  .az-title {
    margin: 0 0 12px !important; color: var(--az-text);
    font-size: clamp(2.1rem, 3.8vw, 3.8rem) !important; font-weight: 620 !important;
    line-height: 1.08 !important; letter-spacing: -.058em !important;
  }
  .az-title span { color: var(--az-amber); }
  .az-subtitle, .desk-subtitle {
    max-width: 700px; color: var(--az-muted); font-size: .91rem; line-height: 1.65;
  }
  .az-status {
    display: inline-flex; align-items: center; gap: 8px; flex-shrink: 0;
    color: #c6d3df; border: 1px solid var(--az-line); border-radius: 99px;
    padding: 7px 11px; background: #121b2599; font: .67rem var(--az-mono);
    letter-spacing: .035em; white-space: nowrap;
  }
  .az-status-dot {
    display: inline-block; width: 6px; height: 6px; flex: 0 0 6px;
    border-radius: 50%; background: var(--az-amber); box-shadow: 0 0 0 3px #edba6812;
  }
  .az-status-dot.is-active { background: var(--az-teal); box-shadow: 0 0 0 3px #6dd8c012; }
  .az-workflow {
    display: flex; flex-wrap: wrap; gap: 10px 24px; align-items: center;
    padding: 14px 0 18px; margin-bottom: 6px; border-bottom: 1px solid var(--az-line);
    color: var(--az-dim); font: .68rem var(--az-mono); letter-spacing: .02em;
  }
  .az-workflow > div, .az-workflow > span { display: flex; align-items: center; gap: 8px; }
  .az-workflow b, .az-workflow strong { color: var(--az-amber); font-weight: 500; }
  .az-workflow .active { color: var(--az-text); }
  .az-section-title {
    display: flex; align-items: center; justify-content: flex-start; gap: 11px;
    margin: 16px 0 2px; color: var(--az-text); font-size: .99rem; font-weight: 600;
    letter-spacing: -.02em;
  }
  .az-section-title small, .az-section-title span {
    color: var(--az-dim); font: .64rem var(--az-mono); letter-spacing: .055em;
  }

  /* Numeric panels: a quiet frame with strong, tabular values. */
  .az-card, [data-testid="stMetric"] {
    min-height: 130px; padding: 19px 20px; border: 1px solid var(--az-line);
    border-radius: 11px; background: linear-gradient(145deg, #16212c 0%, var(--az-panel) 80%);
    box-shadow: 0 5px 18px #0000000c;
  }
  .az-card-label, [data-testid="stMetricLabel"], [data-testid="stMetricLabel"] p {
    display: block; color: var(--az-muted); font-size: .69rem; font-weight: 500;
    line-height: 1.5; letter-spacing: .035em;
  }
  .az-card-value, [data-testid="stMetricValue"] {
    display: block; color: var(--az-text); margin-top: 10px;
    font: 500 clamp(1.45rem, 2.05vw, 2.1rem)/1.25 var(--az-mono);
    font-variant-numeric: tabular-nums; letter-spacing: -.065em;
  }
  .az-card-value small { margin-left: 5px; color: var(--az-dim); font-size: .7rem; letter-spacing: 0; }
  [data-testid="stMetricValue"] [data-testid="stMarkdownContainer"],
  [data-testid="stMetricValue"] [data-testid="stMarkdownContainer"] p {
    font: inherit; color: inherit; line-height: 1.25; margin: 0;
  }
  .az-card-note { display: block; color: var(--az-dim); margin-top: 9px; font-size: .68rem; line-height: 1.45; }
  .az-card.is-accent { border-color: #edba6850; }
  .az-card.is-accent .az-card-value { color: var(--az-amber); }
  .az-micro { color: var(--az-dim); font: .65rem/1.6 var(--az-mono); letter-spacing: .015em; }
  .az-source-strip {
    display: flex; flex-wrap: wrap; align-items: center; gap: 8px 24px;
    padding: 11px 15px; border: 1px solid var(--az-line); border-left: 2px solid var(--az-amber);
    border-radius: 5px; color: var(--az-muted); background: #121b2599;
    font: .67rem/1.55 var(--az-mono); overflow-wrap: anywhere;
  }
  .az-source-strip b, .az-source-strip strong { color: #d1dae2; font-weight: 500; }
  .az-quote-board {
    display: grid; grid-template-columns: repeat(2, minmax(0, 1fr));
    overflow: hidden; border: 1px solid var(--az-line); border-radius: 10px;
    background: var(--az-panel);
  }
  .az-quote-side { padding: 20px 22px; }
  .az-quote-side + .az-quote-side { border-left: 1px solid var(--az-line); }
  .az-quote-side .az-card-label { text-transform: uppercase; font-family: var(--az-mono); letter-spacing: .09em; }
  .az-quote-price {
    margin: 10px 0; font: 500 clamp(1.7rem, 3vw, 2.8rem)/1.2 var(--az-mono);
    font-variant-numeric: tabular-nums; letter-spacing: -.065em;
  }
  .az-bid, .az-bid .az-quote-price { color: var(--az-teal); }
  .az-ask, .az-ask .az-quote-price { color: var(--az-amber); }
  .az-trade-row {
    display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 12px;
    padding: 13px 0; border-bottom: 1px solid var(--az-line); font: .76rem var(--az-mono);
  }
  .az-trade-row:last-child { border-bottom: 0; }
  .az-trade-side { color: var(--az-teal); font-size: .64rem; letter-spacing: .06em; }
  .az-trade-side.sell { color: var(--az-amber); }
  .az-insight {
    padding: 15px 17px; margin: 5px 0; border-left: 2px solid var(--az-teal);
    border-radius: 0 7px 7px 0; background: #6dd8c009; color: #b9cdc9; font-size: .8rem; line-height: 1.6;
  }
  .az-insight strong { color: var(--az-teal); font-weight: 500; }

  /* The empty state is a complete starting screen, with no invented prices. */
  .az-empty {
    padding: 34px; border: 1px solid var(--az-line); border-radius: 12px;
    background: linear-gradient(115deg, #15222e, var(--az-panel)); margin: 2px 0 14px;
  }
  .az-empty h2 { margin: 0 0 10px; font-size: 1.5rem !important; letter-spacing: -.035em; }
  .az-empty p { max-width: 710px; color: var(--az-muted); font-size: .86rem; }
  .az-empty-step {
    min-height: 168px; padding: 23px; border: 1px solid var(--az-line); border-radius: 10px;
    background: var(--az-panel); color: var(--az-muted); font-size: .81rem; line-height: 1.65;
  }
  .az-empty-step > span, .az-empty-step .az-step-number {
    display: block; margin-bottom: 15px; color: var(--az-amber); font: .69rem var(--az-mono);
  }
  .az-empty-step h3, .az-empty-step strong {
    display: block; margin: 0 0 8px; color: var(--az-text); font-size: .93rem; font-weight: 600;
  }
  .az-empty-step p { margin: 0; color: var(--az-muted); font-size: .8rem; }
  .az-footer {
    display: flex; flex-wrap: wrap; justify-content: space-between; gap: 10px 24px;
    border-top: 1px solid var(--az-line); margin-top: 30px; padding-top: 17px;
    color: var(--az-dim); font-size: .67rem; line-height: 1.6;
  }
  .az-footer strong { color: var(--az-muted); font-weight: 500; }
  .desk-rule { height: 1px; background: var(--az-line); margin: 1.2rem 0; }

  /* Native controls retain keyboard behavior and clear focus affordances. */
  [data-testid="stSidebar"] { background: #101923; border-right: 1px solid var(--az-line); }
  [data-testid="stSidebarContent"] { padding-top: 1rem; }
  [data-testid="stSidebarUserContent"] { padding: 1.25rem 1.25rem 2rem; }
  [data-testid="stSidebar"] h3 { font-size: .92rem !important; }
  [data-testid="stWidgetLabel"] p { color: #c4cfd9; font-size: .77rem; }
  [data-testid="stRadio"] label, [data-testid="stCheckbox"] label { color: #c4cfd9; }
  [data-testid="stRadio"] label p, [data-testid="stCheckbox"] label p { font-size: .8rem; }
  [data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="textarea"],
  [data-baseweb="select"] > div {
    background: #111c27 !important; border-color: #324252 !important;
    border-radius: 7px; color: var(--az-text) !important;
  }
  [data-baseweb="input"] input, [data-baseweb="base-input"] input,
  [data-baseweb="textarea"] textarea, [data-baseweb="select"] input {
    color: var(--az-text) !important; caret-color: var(--az-amber); font-size: .83rem;
  }
  [data-baseweb="input"]:focus-within, [data-baseweb="textarea"]:focus-within,
  [data-baseweb="select"]:focus-within > div {
    border-color: var(--az-amber) !important; box-shadow: 0 0 0 2px #edba6818;
  }
  [data-baseweb="input"] input::placeholder, textarea::placeholder { color: var(--az-dim) !important; }
  [data-baseweb="select"] svg { fill: var(--az-muted); }
  [data-baseweb="popover"] [role="listbox"], [data-baseweb="menu"] {
    color: var(--az-text); background: var(--az-raised); border: 1px solid var(--az-line);
  }
  [data-baseweb="popover"] [role="option"] { color: var(--az-text); background: var(--az-raised); }
  [data-baseweb="popover"] [role="option"]:hover,
  [data-baseweb="popover"] [aria-selected="true"] { background: #293749; }
  [data-testid="stNumberInput"] button { color: var(--az-muted); background: #172431; }
  [data-testid="stButton"] button, [data-testid="stDownloadButton"] button,
  [data-testid="stFormSubmitButton"] button, [data-testid="stFileUploaderDropzone"] button {
    min-height: 38px; border: 1px solid #354656; border-radius: 7px;
    background: #192735; color: #dae3ea; font-size: .79rem; font-weight: 500;
    transition: background .15s ease, border-color .15s ease, color .15s ease;
  }
  [data-testid="stButton"] button:hover, [data-testid="stDownloadButton"] button:hover,
  [data-testid="stFormSubmitButton"] button:hover, [data-testid="stFileUploaderDropzone"] button:hover {
    color: var(--az-amber); border-color: #edba687a; background: #242d35;
  }
  [data-testid="stButton"] button[kind="primary"],
  [data-testid="stFormSubmitButton"] button[kind="primary"] {
    color: #111821; background: var(--az-amber); border-color: var(--az-amber); font-weight: 650;
  }
  [data-testid="stButton"] button[kind="primary"]:hover,
  [data-testid="stFormSubmitButton"] button[kind="primary"]:hover { background: #f4cd8f; color: #111821; }
  button:focus-visible, a:focus-visible { outline: 2px solid var(--az-amber) !important; outline-offset: 3px; }
  [data-testid="stButton"] button:disabled, [data-testid="stDownloadButton"] button:disabled,
  [data-testid="stFormSubmitButton"] button:disabled {
    color: #687b8b; background: #131e28; border-color: #263440; cursor: not-allowed; opacity: .72;
  }
  [data-testid="stFileUploaderDropzone"] {
    padding: 16px 12px; background: #0d1721; border: 1px dashed #344a5d; border-radius: 8px;
  }
  [data-testid="stFileUploaderDropzone"] span { color: #bdcbd7; font-size: .77rem; }
  [data-testid="stFileUploaderDropzone"] small { color: var(--az-dim); font-size: .66rem; }
  [data-testid="stFileUploaderDropzone"] svg { color: var(--az-dim); }
  [data-testid="stFileUploaderFile"] { background: var(--az-panel); border-radius: 6px; }
  [data-testid="stTabs"] [data-baseweb="tab-list"] {
    gap: 6px; border-bottom: 1px solid var(--az-line); background: transparent; padding-bottom: 0;
  }
  [data-testid="stTabs"] [data-baseweb="tab"] {
    padding: 10px 15px 12px; height: auto; color: var(--az-muted);
    border-radius: 7px 7px 0 0; background: transparent; font-size: .79rem;
  }
  [data-testid="stTabs"] [data-baseweb="tab"] p { font-size: .79rem; }
  [data-testid="stTabs"] [aria-selected="true"] { color: var(--az-amber); background: #edba6808; }
  [data-testid="stTabs"] [data-baseweb="tab-highlight"] { height: 2px; background: var(--az-amber); }
  [data-testid="stTabs"] [data-baseweb="tab-border"] { background: transparent; }
  [data-testid="stTabs"] [data-baseweb="tab-panel"] { padding-top: 21px; }
  [data-testid="stExpander"] { border-color: var(--az-line); background: #121b2570; border-radius: 8px; }
  [data-testid="stExpander"] details { border-color: var(--az-line); }
  [data-testid="stExpander"] summary { color: #b7c6d3; font-size: .8rem; }
  [data-testid="stExpander"] summary:hover { color: var(--az-amber); }
  [data-testid="stVerticalBlockBorderWrapper"] > div { border-color: var(--az-line); border-radius: 10px; }
  [data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--az-line); border-radius: 8px; overflow: hidden;
  }
  [data-testid="stTable"] th { background: #182430; color: var(--az-muted); font-size: .71rem; }
  [data-testid="stTable"] td { color: #d8e1e8; font: .76rem var(--az-mono); border-color: var(--az-line); }
  [data-testid="stAlert"] { border-radius: 8px; font-size: .79rem; }
  [data-testid="stAlert"] p { font-size: .8rem; line-height: 1.6; }
  [data-testid="stPlotlyChart"] { border: 1px solid var(--az-line); border-radius: 10px; overflow: hidden; }
  [data-testid="stJson"] { border: 1px solid var(--az-line); border-radius: 8px; }

  @media (max-width: 900px) {
    .block-container { padding-right: 1.5rem; padding-left: 1.5rem; }
    .az-hero { gap: 20px; padding-top: 20px; }
    .az-title { font-size: 2.8rem !important; }
    .az-credit { font-size: .65rem; }
    .az-card, [data-testid="stMetric"] { padding: 16px; min-height: 120px; }
    .az-card-value, [data-testid="stMetricValue"] { font-size: 1.55rem; }
    .az-empty { padding: 26px; }
    .az-empty-step { padding: 18px; }
    [data-testid="stTabs"] [data-baseweb="tab"] { padding-right: 11px; padding-left: 11px; }
  }
  @media (max-width: 640px) {
    .block-container { padding: 1.8rem 1rem 1.4rem; }
    [data-testid="stMainBlockContainer"] { padding-top: 1.8rem; }
    .az-topbar { flex-wrap: wrap; gap: 10px; padding-bottom: 15px; }
    .az-monogram { width: 32px; height: 32px; flex-basis: 32px; font-size: .8rem; }
    .az-wordmark { font-size: .63rem; letter-spacing: .1em; }
    .az-credit { font-size: .61rem; }
    .az-hero { display: flex; flex-direction: column; gap: 15px; padding: 20px 0 17px; }
    .az-title { font-size: 2.45rem !important; }
    .az-subtitle { font-size: .83rem; }
    .az-status { font-size: .61rem; }
    .az-workflow { gap: 10px 16px; font-size: .6rem; }
    .az-card, [data-testid="stMetric"] { min-height: 108px; }
    .az-card-value, [data-testid="stMetricValue"] { font-size: 1.8rem; }
    .az-section-title { align-items: center; gap: 9px; }
    .az-empty { padding: 23px; }
    .az-empty-step { min-height: 0; }
    .az-quote-side { padding: 17px; }
    .az-quote-price { font-size: 1.85rem; }
    .az-source-strip { gap: 7px 16px; font-size: .61rem; }
    .az-footer { margin-top: 24px; font-size: .62rem; }
    [data-testid="stTabs"] [data-baseweb="tab-list"] { gap: 0; overflow-x: auto; }
    [data-testid="stTabs"] [data-baseweb="tab"] { white-space: nowrap; padding: 9px 11px 11px; }
    [data-testid="stTabs"] [data-baseweb="tab"] p { font-size: .72rem; }
  }
  @media (prefers-reduced-motion: reduce) {
    [data-testid="stButton"] button, [data-testid="stDownloadButton"] button,
    [data-testid="stFormSubmitButton"] button { transition: none; }
  }
</style>
"""


def plain(value: Any) -> Any:
    """Convert result objects to display/JSON values without inventing fields."""
    if is_dataclass(value) and not isinstance(value, type):
        return plain(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(item) for item in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def json_bytes(value: Any) -> bytes:
    return json.dumps(plain(value), indent=2, allow_nan=False).encode("utf-8")


def parse_volatility_upload(data: bytes, filename: str) -> tuple[VolNode, ...]:
    """Read explicit base-unit nodes; display percentages are never inferred."""
    if filename.lower().endswith(".json"):
        payload = json.loads(data.decode("utf-8-sig"))
        if isinstance(payload, list):
            return tuple(VolNode.from_dict(row) for row in payload)
        return VolVersion.from_dict(payload).nodes
    frame = pd.read_csv(StringIO(data.decode("utf-8-sig")))
    nodes = []
    for record in frame.to_dict(orient="records"):
        clean = {key: value for key, value in record.items() if not pd.isna(value)}
        clean["underlyings"] = tuple(json.loads(clean["underlyings"]))
        nodes.append(VolNode.from_dict(clean))
    return tuple(nodes)


def record_table(value: Any) -> pd.DataFrame:
    data = plain(value)
    if isinstance(data, list):
        return pd.DataFrame(data)
    if isinstance(data, dict):
        return pd.DataFrame([data])
    return pd.DataFrame()


def node_figure(
    rows: list[dict[str, Any]],
    *,
    x: str,
    y: str,
    group: str,
    title: str,
    y_label: str,
) -> go.Figure:
    """Plot observed/edited nodes only; never fabricate a market surface."""
    frame = pd.DataFrame(rows)
    figure = go.Figure()
    if not frame.empty:
        groups = frame.groupby(group, sort=False) if group in frame else [("Nodes", frame)]
        palette = ["#edba68", "#6dd8c0", "#96aabc", "#b0a8dc", "#c2c9a0"]
        states = {
            "market": ("#96aabc", "solid", "circle-open", 11, 4),
            "draft": ("#edba68", "dash", "diamond-open", 9, 2.5),
            "active": ("#6dd8c0", "dot", "circle", 5, 2),
        }
        for index, (name, points) in enumerate(groups):
            points = points.sort_values(x)
            color, dash, symbol, size, width = states.get(
                str(name).lower(), (palette[index % len(palette)], "solid", "circle", 6, 2)
            )
            figure.add_trace(
                go.Scatter(
                    x=points[x], y=points[y], mode="lines+markers", name=str(name),
                    line={"color": color, "width": width, "dash": dash},
                    marker={"color": color, "size": size, "symbol": symbol, "line": {"width": 1.5}},
                    hovertemplate="%{x}<br>%{y:.4f}<extra>%{fullData.name}</extra>",
                )
            )
    figure.update_layout(
        title={"text": title, "font": {"size": 13, "color": "#edf0f2"}, "x": .045},
        template="plotly_dark", paper_bgcolor="#121b25", plot_bgcolor="#121b25",
        height=320, margin={"l": 24, "r": 24, "t": 54, "b": 20},
        xaxis_title=x.replace("_", " ").title(), yaxis_title=y_label,
        legend={"orientation": "h", "y": 1.02, "x": 1, "xanchor": "right", "yanchor": "bottom", "font": {"size": 10}},
        font={"family": "-apple-system, BlinkMacSystemFont, Segoe UI, sans-serif", "color": "#99aab9", "size": 11},
        hovermode="x unified",
        hoverlabel={"bgcolor": "#1b2937", "bordercolor": "#34495b", "font": {"color": "#edf0f2", "size": 12}},
    )
    axis_style = {
        "gridcolor": "#253441", "zerolinecolor": "#405364", "linecolor": "#344656",
        "tickfont": {"family": "SFMono-Regular, Consolas, monospace", "size": 10, "color": "#99aab9"},
        "title_font": {"size": 10, "color": "#718597"}, "showline": False, "ticks": "",
    }
    figure.update_xaxes(**axis_style, showgrid=False)
    figure.update_yaxes(**axis_style, griddash="dot")
    if not frame.empty:
        low, high = float(frame[y].min()), float(frame[y].max())
        pad = max((high - low) * 0.15, abs((high + low) / 2) * 0.01, 1e-6)
        figure.update_yaxes(range=[max(0, low - pad) if low >= 0 else low - pad, high + pad])
    return figure
