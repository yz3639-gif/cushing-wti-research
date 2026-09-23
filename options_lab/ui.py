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
  .stApp { background: #101419; color: #e4e8ec; }
  [data-testid="stHeader"] { background: #101419; }
  [data-testid="stSidebar"] { background: #161c23; }
  .block-container { max-width: 1480px; padding-top: 4rem; }
  h1 { font-size: 2.1rem !important; letter-spacing: -.04em; }
  h2, h3 { letter-spacing: -.025em; }
  [data-testid="stMetric"] {
    padding: 14px 16px; border: 1px solid #2b343f;
    border-radius: 8px; background: #161c23;
  }
  [data-testid="stMetricLabel"] { color: #99a8b6; }
  [data-testid="stMetricValue"] { font-size: 1.6rem; }
  [data-testid="stVerticalBlockBorderWrapper"] { border-color: #2b343f; }
  .desk-kicker { color: #9caebd; font-size: .74rem;
    letter-spacing: .15em; text-transform: uppercase; margin-bottom: .5rem; }
  .desk-subtitle { color: #aebac4; margin-top: -.5rem; margin-bottom: 1.6rem; }
  .desk-rule { height: 1px; background: #2b343f; margin: 1.2rem 0; }
  code { color: #b9d9e0; }
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
        palette = ["#91bec7", "#d6b885", "#b2a2cb", "#9baaad", "#91ad8c"]
        for index, (name, points) in enumerate(groups):
            points = points.sort_values(x)
            figure.add_trace(
                go.Scatter(
                    x=points[x], y=points[y], mode="lines+markers", name=str(name),
                    line={"color": palette[index % len(palette)], "width": 2},
                    marker={"size": 7},
                    hovertemplate="%{x}<br>%{y:.4f}<extra>%{fullData.name}</extra>",
                )
            )
    figure.update_layout(
        title={"text": title, "font": {"size": 15}},
        template="plotly_dark", paper_bgcolor="#101419", plot_bgcolor="#101419",
        height=300, margin={"l": 20, "r": 20, "t": 50, "b": 20},
        xaxis_title=x.replace("_", " ").title(), yaxis_title=y_label,
        legend={"orientation": "h", "y": -0.25},
        font={"family": "sans-serif", "color": "#b8c4ce"},
    )
    figure.update_xaxes(gridcolor="#25303a", zerolinecolor="#35434f")
    figure.update_yaxes(gridcolor="#25303a", zerolinecolor="#35434f")
    return figure
