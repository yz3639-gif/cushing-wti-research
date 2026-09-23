"""Read-only presentation of the desk's existing versioned results."""

from html import escape
from typing import Any

import plotly.graph_objects as go
import streamlit as st

from options_lab.ui import node_figure


def html(markup: str) -> None:
    st.markdown(markup, unsafe_allow_html=True)


def number(value: Any, digits: int = 2, prefix: str = "") -> str:
    return "Unavailable" if value is None else f"{prefix}{value:,.{digits}f}"


def effective_target(desk: Any) -> str:
    return desk.settings.target_id or next((c.contract_id for c in desk.market.contracts if c.kind == "cso"), "")


def valid_hedge(hedge: dict[str, Any]) -> bool:
    return hedge.get("status") in {"optimal", "feasible_limit", "fallback_zero", "fallback_futures"} and hedge.get("position_constraints", {}).get("compliant") is True


def contract_label(contract: Any) -> str:
    """Short presentation name; exact identities remain in the detailed tables."""
    if contract is None:
        return "Unavailable contract"
    if contract.kind == "future":
        return contract.contract_id.removeprefix("FIXTURE_")
    legs = " / ".join(leg.removeprefix("FIXTURE_") for leg in contract.underlyings)
    return f"{legs} · {contract.strike:g} {contract.right.title()}"


def masthead(*, loaded: bool = False) -> None:
    hero_class = "az-hero is-loaded" if loaded else "az-hero"
    html('<div class="az-topbar"><div class="az-monogram">AZ</div>'
         '<div class="az-wordmark">ANTONY ZUO<span>ENERGY DERIVATIVES</span></div>'
         '<div class="az-credit">Built by <strong>Antony Zuo</strong></div></div>'
         f'<div class="{hero_class}"><div><div class="az-eyebrow">WTI / OPTIONS &amp; VOLATILITY</div>'
         '<h1 class="az-title">The options desk.</h1>'
         '<p class="az-subtitle">Shape the volatility. Price the trade. See the risk.</p></div>'
         '<div class="az-status"><span class="az-status-dot"></span> RESEARCH PROTOTYPE</div></div>')


def footer() -> None:
    html('<div class="az-footer"><span>AZ / WTI OPTIONS DESK</span>'
         '<span>Built by Antony Zuo · Model quotes only · No order routing</span></div>')


def section(index: str, title: str, note: str = "") -> None:
    html(f'<div class="az-section-title"><span>{escape(index)}</span> {escape(title)}</div>')
    if note:
        st.caption(note)


def quote_board(desk: Any, bundle: dict[str, Any]) -> None:
    target_id = effective_target(desk)
    quote = next((q for q in bundle.get("quotes", []) if q["contract_id"] == target_id), None)
    target = desk.market.contract_map.get(target_id)
    section("02", "Indicative quote", contract_label(target))
    if quote is None:
        st.info("No quote available for the selected contract.")
        return
    sides = []
    for side in ("bid", "ask"):
        size = quote.get(f"{side}_size", 0)
        available = quote.get(side) is not None and size is not None and size > 0
        price = number(quote.get(side), 2) if available else "—"
        note = f"{size:g} lot{'s' if size != 1 else ''}" if available else "Suppressed · no size"
        sides.append(f'<div class="az-quote-side az-{side}"><div class="az-card-label">{side.upper()} / USD per bbl</div>'
                     f'<div class="az-quote-price">{price}</div><div class="az-card-note">{note}</div></div>')
    html('<div class="az-quote-board">' + "".join(sides) + '</div>')
    st.caption("Model-generated prices and sizes; no execution or fill is implied.")
    for reason in quote.get("reasons", []):
        st.warning(str(reason))
    if quote.get("initial_position_breach"):
        st.warning("The starting position exceeds its limit. Only the complete indicated repair clip can be compliant; inspect the quote explanation.")
    inventory = quote.get("inventory")
    html(f'<div class="az-source-strip"><span>Position <strong>{number(inventory, 0)} lots</strong></span>'
         f'<span>Direct CSO hedge <strong>{"Allowed" if desk.settings.allow_cso_hedge else "Blocked"}</strong></span></div>')
    with st.expander("Why this quote?"):
        st.write("The model value is adjusted for crossing cost and the exact change in inventory stress risk. Position and risk limits determine the displayed size.")
        st.write(f"Bid risk change: {number(quote.get('bid_marginal_risk'), 2, '$')}. Ask risk change: {number(quote.get('ask_marginal_risk'), 2, '$')}.")
        st.caption(quote.get("position_policy", ""))


def risk_figure(summary: dict[str, Any]) -> go.Figure:
    names, values, colors = [], [], []
    for key, name, color in [("unhedged", "Unhedged", "#e58f86"), ("delta", "Futures only", "#8498ad"), ("proxy", "Futures + options", "#6dd8c0")]:
        value = summary.get(key, {}).get("worst_loss")
        if value is not None:
            names.append(name)
            values.append(value)
            colors.append(color)
    figure = go.Figure(go.Bar(x=values, y=names, orientation="h", marker_color=colors,
                             text=[f"${v:,.0f}" for v in values], textposition="outside", cliponaxis=False,
                             hovertemplate="%{y}<br>Worst scenario loss: $%{x:,.2f}<extra></extra>"))
    figure.update_layout(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                         height=225, margin={"l": 0, "r": 65, "t": 15, "b": 35},
                         font={"family": "Arial, sans-serif", "color": "#a9b9c8", "size": 12},
                         xaxis={"title": "Worst loss across the configured scenarios · USD", "gridcolor": "#243240", "zeroline": False},
                         yaxis={"autorange": "reversed", "showgrid": False}, showlegend=False, bargap=.47)
    if values:
        figure.update_xaxes(range=[min(0, min(values) * 1.2), max(1, max(values) * 1.27)])
    return figure


def overview(desk: Any) -> None:
    """No new pricing or aggregation engine: show only the current result bundle."""
    bundle = desk.bundle
    if not bundle:
        st.info("No valid valuation bundle available. Review the data or calculation error.")
        return
    status = bundle.get("status", "unknown")
    if status not in ("ok", "analysis_only"):
        st.warning(f"Calculation status: {status.replace('_', ' ')}")
    for warning in bundle.get("warnings", []):
        if not str(warning).startswith("engineering_fixture:"):
            st.warning(str(warning))
    target_id = effective_target(desk)
    contract = desk.market.contract_map.get(target_id)
    target = next((p for p in bundle.get("prices", []) if p["contract_id"] == target_id), {})
    summary = bundle.get("risk", {}).get("summary", {})
    proxy = bundle.get("hedges", {}).get("proxy", {})
    proxy_valid = valid_hedge(proxy)
    visible_summary = {key: value for key, value in summary.items() if key == "unhedged" or valid_hedge(bundle.get("hedges", {}).get(key, {}))}
    cards = st.columns(4)
    kind = contract.kind if contract else "cso"
    cards[0].metric(f"{kind.upper()} model value · USD/bbl", number(target.get("model_price"), 4, "$"))
    if kind == "cso":
        cards[1].metric("CSO normal vol · $/bbl/√yr", number(target.get("vol"), 3))
    else:
        vol = target.get("vol")
        cards[1].metric("Vanilla lognormal vol · annualized %", number(vol * 100 if vol is not None and kind == "vanilla" else None, 2))
    cards[2].metric("Proxy hedge · worst scenario loss", number(visible_summary.get("proxy", {}).get("worst_loss"), 0, "$"))
    cards[3].metric("Proxy hedge · estimated cost", number(proxy.get("cost") if proxy_valid else None, 2, "$"))

    left, right = st.columns([1.45, 1], gap="large")
    with left, st.container(border=True):
        section("01", "Volatility at a glance", "Current contract slice · only supplied nodes are shown")
        rows = []
        for state, version in [("Market", desk.market_vol), ("Active", desk.active_vol)]:
            for node in version.nodes:
                if contract is not None and node.slice_key == contract.slice_key:
                    rows.append({"strike": node.strike, "volatility": node.value * (100 if kind == "vanilla" else 1), "state": state})
        if rows:
            chart = node_figure(rows, x="strike", y="volatility", group="state", title="CSO · normal volatility" if kind == "cso" else "Vanilla · lognormal volatility", y_label="USD/bbl / √year" if kind == "cso" else "annualized %")
            st.plotly_chart(chart, use_container_width=True, key=f"overview_vol_{bundle['bundle_id']}", config={"displayModeBar": False})
        else:
            st.info("No covered volatility slice available for this contract.")
        st.caption("Open Volatility workspace to edit a draft, preview the change and apply it.")
    with right, st.container(border=True):
        quote_board(desk, bundle)

    left, right = st.columns([1.45, 1], gap="large")
    with left, st.container(border=True):
        section("03", "What remains at risk", "Same scenarios and portfolio · hedge results include estimated costs")
        st.plotly_chart(risk_figure(visible_summary), use_container_width=True, key=f"overview_risk_{bundle['bundle_id']}", config={"displayModeBar": False})
        for key in ("delta", "proxy"):
            if key not in visible_summary:
                st.warning(f"{'Futures-only' if key == 'delta' else 'Proxy'} hedge unavailable: {bundle.get('hedges', {}).get(key, {}).get('status', 'unavailable')}. No hedge risk result is shown.")
        basis = next((s for s in bundle.get("risk", {}).get("scenarios", []) if s["name"] == "cso_basis_vol_up_1_normal"), None)
        if basis and proxy_valid:
            html('<div class="az-insight"><div class="az-eyebrow">THE RISK A PROXY CAN MISS</div>'
                 '<strong>CSO normal vol +1.0 $/bbl/√year.<br>Vanilla vol unchanged.</strong>'
                 f'<p>Residual proxy P&amp;L: <strong>{number(basis.get("proxy_net"), 0, "$ ")}</strong></p></div>')
            st.caption(f"Scenario horizon: {desk.settings.horizon_days:g} day(s); includes time decay.")
        st.caption("Hypothetical stress losses, not a probability estimate or a bound on future loss.")
    with right, st.container(border=True):
        section("04", "The hedge ticket", "Whole contracts · futures and options, including CSO" if desk.settings.allow_cso_hedge else "Whole contracts · futures and vanilla options")
        solver_status = proxy.get("status", "unavailable")
        st.caption(f"Solver: {solver_status.replace('_', ' ')}" + (" · feasible, not proved optimal" if solver_status == "feasible_limit" else ""))
        if not proxy_valid:
            st.warning("No valid hedge proposal. Review data, solver status and position limits.")
            if proxy.get("message"):
                st.caption(proxy["message"])
        else:
            trades = proxy.get("trades", [])
            for trade in trades:
                side = "BUY" if trade["quantity"] > 0 else "SELL"
                label = contract_label(desk.market.contract_map.get(trade["contract_id"]))
                html(f'<div class="az-trade-row"><span class="az-trade-side az-{"bid" if side == "BUY" else "ask"}">{side}</span>'
                     f'<span title="{escape(trade["contract_id"], quote=True)}">{escape(label)}</span>'
                     f'<strong>{abs(trade["quantity"]):g}</strong></div>')
            if not trades:
                st.info("Zero-trade baseline: no hedge trades proposed for this bundle.")
        st.caption("Exact contract identifiers, constraints and per-leg costs are available in Valuation & risk.")
    st.caption(f"One calculation · {bundle['bundle_id']} · Active volatility {bundle['vol_version_id']}")
