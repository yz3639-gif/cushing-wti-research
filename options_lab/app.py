"""Local WTI options desk. Run with ``streamlit run options_lab/app.py``."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace
from html import escape
import json
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from options_lab.ui import DESK_CSS, json_bytes, node_figure, plain, parse_volatility_upload
from options_lab.models import MarketSnapshot, Position, Settings, VolNode
from options_lab.presentation import footer, html, masthead, overview, valid_hedge

FIXTURE_LABEL = "Engineering fixture – not market data"


def volatility_rows(version: Any) -> list[dict[str, Any]]:
    if version is None:
        return []
    return [
        {
            "Node": node.node_id,
            "Product": node.product,
            "Underlyings": " / ".join(node.underlyings),
            "Expiry": node.expiry,
            "Strike": node.strike,
            "Model": node.model,
            "Volatility": node.value * (100.0 if node.model == "lognormal" else 1.0),
            "Unit": "annualized %" if node.model == "lognormal" else "USD/bbl / √year",
            "Origin": node.origin,
            "Source": node.source,
            "As of": node.as_of,
        }
        for node in version.nodes
    ]


def volatility_charts(versions: dict[str, Any]) -> None:
    available = [version for version in versions.values() if version is not None]
    slice_keys = sorted(
        {node.slice_key for version in available for node in version.nodes}, key=str
    )
    if not slice_keys:
        st.caption("No volatility nodes available.")
        return
    chosen = st.selectbox(
        "Volatility slice", slice_keys,
        format_func=lambda key: f"{key[0]} · {' / '.join(key[1])} · {key[2][:10]} · {key[3]}",
        key="vol_slice",
    )
    rows = []
    for state, version in versions.items():
        if version:
            for node in version.nodes:
                if node.slice_key == chosen:
                    rows.append({
                        "strike": node.strike,
                        "volatility": node.value * (100 if node.model == "lognormal" else 1),
                        "state": state,
                    })
    is_normal = chosen[3] == "normal"
    st.plotly_chart(
        node_figure(
            rows, x="strike", y="volatility", group="state",
            title="Normal volatility nodes" if is_normal else "Lognormal volatility nodes",
            y_label="USD/bbl / √year" if is_normal else "annualized %",
        ),
        use_container_width=True,
    )
    st.caption("Lines connect supplied nodes for inspection. They do not imply additional market observations.")


def volatility_table(version: Any) -> None:
    normal, lognormal = st.tabs(["CSO · normal", "Vanilla · lognormal"])
    for model, panel in [("normal", normal), ("lognormal", lognormal)]:
        with panel:
            st.caption("Normal volatility · USD/bbl / √year" if model == "normal" else "Lognormal volatility · annualized %")
            st.dataframe([row for row in volatility_rows(version) if row["Model"] == model], hide_index=True, use_container_width=True)


def source_panel() -> tuple[str, Any] | None:
    """An explicit source action; a selected mode alone never loads data."""
    st.sidebar.markdown('<div class="az-eyebrow">AZ / WORKSPACE</div>', unsafe_allow_html=True)
    st.sidebar.markdown("### Data & session")
    source = st.sidebar.radio(
        "Source mode",
        ["Authorized local data", FIXTURE_LABEL, "Live connection"],
        key="source_mode",
    )
    if source == "Authorized local data":
        st.sidebar.caption("Load an authorized snapshot or replay from this computer.")
        uploaded = st.sidebar.file_uploader(
            "Snapshot or replay", type=["json", "csv"], key="local_upload"
        )
        if st.sidebar.button("Load local file", disabled=uploaded is None, key="load_local"):
            return "file", uploaded
    elif source == FIXTURE_LABEL:
        st.sidebar.warning(FIXTURE_LABEL)
        st.sidebar.caption("Synthetic inputs for workflow inspection and tests only.")
        if st.sidebar.button("Load engineering fixture", key="load_fixture"):
            return "fixture", None
    else:
        st.sidebar.info("Connection pending — no authorized live adapter configured.")
        st.sidebar.caption("No market connection or order-routing session is active.")
    with st.sidebar.expander("Save / restore full session"):
        st.caption("Local JSON only. Restore validates inputs and recomputes results before replacing this desk. Playback always resumes paused.")
        session_file = st.file_uploader("Saved desk session", type=["json"], key="session_upload")
        if st.button("Restore full session", disabled=session_file is None, key="restore_session"):
            return "session", session_file
        if "desk" in st.session_state:
            from options_lab.session_io import export_session
            try:
                saved = export_session(st.session_state["desk"],
                    replay_snapshots=st.session_state.get("replay_snapshots", ()),
                    replay_index=st.session_state.get("replay_index", 0),
                    replay_failures=st.session_state.get("replay_failures", ()),
                    source_sha256=st.session_state.get("source_sha256", ""),
                    import_warnings=st.session_state.get("import_warnings", ()),
                    fixture_selected=st.session_state.get("fixture_selected", False))
                st.download_button("Download full session", saved, file_name="wti-desk-session.json", mime="application/json", key="download_session")
            except (ValueError, TypeError, KeyError) as exc:
                st.error(f"Session export unavailable: {exc}")
    return None


def onboarding() -> None:
    html('<div class="az-empty"><div class="az-eyebrow">FROM A VOLATILITY VIEW TO A HEDGE DECISION</div>'
         '<h2>One change.<br>Every consequence.</h2>'
         '<p>Adjust a calendar-spread volatility input. Inspect the quote, the proxy hedge, '
         'and the exposure that remains.</p></div>')
    left, right = st.columns([1, 2])
    if left.button("Explore the synthetic demo →", key="explore_demo", type="primary", use_container_width=True):
        if run_action(lambda: install_source("fixture", None), "Synthetic demonstration loaded."):
            st.session_state["pending_source_mode"] = FIXTURE_LABEL
            st.rerun()
    right.caption("Synthetic inputs only. To use your own snapshot, open Data & session in the sidebar.")
    st.info("No market snapshot loaded. Choose an authorized local file to begin, or explicitly load the synthetic demo.")
    left, middle, right = st.columns(3)
    with left, st.container(border=True):
        st.markdown("**01 / Shape the volatility**")
        st.write("Stage a change. Compare it against the same market before applying.")
    with middle, st.container(border=True):
        st.markdown("**02 / Build the proxy hedge**")
        st.write("See buy and sell quantities in whole contracts, with estimated costs.")
    with right, st.container(border=True):
        st.markdown("**03 / Challenge the risk**")
        st.write("Stress the spread and volatility independently. Inspect what remains.")
    st.caption("An engineering fixture is available only through its explicit source mode.")
    with st.expander("What the desk expects"):
        st.write(
            "Use an authorized local snapshot with named futures contracts, "
            "option definitions, timestamps, and volatility inputs. Daily settlements "
            "are suitable for snapshot analysis; they are not an executable bid/ask "
            "or an intraday market tape."
        )
        st.write(
            "The live connection remains pending. No prices or hedges are filled "
            "with placeholder results."
        )


def render_payload(title: str, payload: Any) -> None:
    """Display an actual result object while retaining every model field."""
    data = plain(payload)
    st.markdown(f"**{title}**")
    if data is None:
        st.caption("Unavailable for this bundle.")
    elif isinstance(data, list) and all(isinstance(item, dict) for item in data):
        if data:
            st.dataframe(pd.DataFrame(data), hide_index=True, use_container_width=True)
        else:
            st.caption("No rows in this bundle.")
    elif isinstance(data, dict) and all(not isinstance(item, (dict, list)) for item in data.values()):
        st.dataframe(
            pd.DataFrame({"Field": list(data), "Value": [str(value) for value in data.values()]}),
            hide_index=True, use_container_width=True,
        )
    else:
        st.json(data, expanded=False)


def run_action(action: Any, success: str) -> bool:
    try:
        action()
    except (ValueError, KeyError, TypeError) as exc:
        st.session_state["desk_notice"] = ("error", str(exc))
        return False
    st.session_state["desk_notice"] = ("success", success)
    return True


def render_result(bundle: dict[str, Any], *, preview: bool = False) -> None:
    """Every panel is rendered from the same immutable evaluation result."""
    if not bundle:
        st.info("No valid valuation bundle available.")
        return
    label = "Preview bundle" if preview else "Active bundle"
    st.caption(f"{label} · {bundle.get('bundle_id', 'unavailable')} · {bundle.get('as_of', 'unavailable')}")
    status = bundle.get("status", "unknown")
    if status != "ok":
        st.warning(f"Bundle status: {status.replace('_', ' ')}")
    for warning in bundle.get("warnings", []):
        st.warning(str(warning))
    prices = bundle.get("prices", [])
    target = next((row for row in prices if row.get("kind") == "cso" and row.get("position", 0)), None)
    if target is None:
        target = next((row for row in prices if row.get("kind") == "cso"), None)
    summary = bundle.get("risk", {}).get("summary", {})
    metrics = st.columns(4)
    if target is not None and target.get("model_price") is not None:
        metrics[0].metric("CSO model value · USD/bbl", f"{target['model_price']:,.4f}")
    else:
        metrics[0].metric("CSO model value · USD/bbl", "Unavailable")
    for column, strategy, name in zip(metrics[1:], ["unhedged", "delta", "proxy"], ["Unhedged", "Futures-only hedge", "Proxy hedge"]):
        loss = summary.get(strategy, {}).get("worst_loss")
        if strategy != "unhedged" and not valid_hedge(bundle.get("hedges", {}).get(strategy, {})):
            loss = None
        column.metric(f"{name} · worst scenario loss", "Unavailable" if loss is None else f"${loss:,.0f}")
    price_tab, quote_tab, hedge_tab, risk_tab = st.tabs(["Pricing", "Modeled quotes", "Integer hedges", "Stress"])
    with price_tab:
        st.caption("Option premiums: USD/bbl. Greeks retain their explicit model units.")
        render_payload("Contract valuation", prices)
    with quote_tab:
        st.caption("Model-generated quotes. Null prices or zero sizes mean suppressed; no execution is implied.")
        for quote in bundle.get("quotes", []):
            if quote.get("initial_position_breach"):
                st.warning(f"{quote['contract_id']}: starting position exceeds its limit. Minimum complete compliant clip: buy {quote.get('minimum_compliant_bid_clip', 0)}, sell {quote.get('minimum_compliant_ask_clip', 0)} contracts. Zero means no compliant clip. Smaller partial fills are not a compliant repair.")
                st.caption(quote.get("position_policy", ""))
        render_payload("Quote proposals", bundle.get("quotes", []))
    with hedge_tab:
        left, right = st.columns(2)
        for container, key, title in [(left, "delta", "Futures-only hedge"), (right, "proxy", "Proxy hedge")]:
            with container, st.container(border=True):
                hedge = bundle.get("hedges", {}).get(key, {})
                st.markdown(f"**{title}**")
                st.caption(f"Status: {hedge.get('status', 'unavailable')}")
                constraints = hedge.get("position_constraints", {})
                if constraints.get("compliant") is False or hedge.get("status") == "infeasible":
                    st.error("No compliant hedge proposal: the full final portfolio must satisfy position limits.")
                if hedge.get("message"):
                    st.write(hedge["message"])
                if not valid_hedge(hedge):
                    st.warning("No usable hedge proposal. Any retained numerical arrays below are diagnostic baselines, not a completed hedge.")
                render_payload("Trades · integer contracts", hedge.get("trades", []))
                render_payload("Cost and residual risk · USD", {k: v for k, v in hedge.items() if k not in ["trades", "message", "status"]})
    with risk_tab:
        risk = bundle.get("risk", {})
        scenarios = risk.get("scenarios", [])
        if scenarios:
            frame = pd.DataFrame(scenarios)
            columns = [key for key in ["unhedged", "delta_net", "proxy_net"] if key in frame and (key == "unhedged" or valid_hedge(bundle.get("hedges", {}).get(key.removesuffix("_net"), {})))]
            if "name" in frame and columns:
                named = frame.loc[frame["category"] != "joint"] if "category" in frame else frame
                plotted = named.set_index("name")[columns].rename(columns={"unhedged": "Unhedged", "delta_net": "Futures-only · net", "proxy_net": "Proxy · net"})
                st.bar_chart(plotted, color=["#7f909d", "#bca174", "#8bbbc4"][:len(columns)], stack=False, sort=False, y_label="Scenario P&L · USD")
                st.caption("Named scenarios shown above. The full scenario grid remains in the detail table below.")
        st.caption("Hypothetical scenario P&L in USD. Net includes the costs reported in this bundle.")
        render_payload("Scenario detail", [{({"delta_gross": "futures_only_gross", "delta_net": "futures_only_net"}.get(key, key)): value for key, value in row.items()} for row in scenarios])
        render_payload("Risk summary", summary)
        st.caption(f"Reconciliation error: {risk.get('reconciliation_error', 'unavailable')}")
        with st.expander("Scenario assumptions"):
            st.json(plain(bundle.get("scenario_assumptions", [])))
    with st.expander("Atomic versions and full result"):
        for key in ["bundle_id", "snapshot_id", "vol_version_id", "portfolio_id", "settings_id", "engine_version"]:
            st.text(f"{key}: {bundle.get(key, 'unavailable')}")
        st.json(plain(bundle), expanded=False)
        identity = bundle.get("bundle_id", "failed-result")
        st.download_button("Download this result bundle", json_bytes(bundle), file_name=f"{identity}.json", mime="application/json", key=f"download_{'preview' if preview else 'active'}_{identity}")


def render_preview_comparison(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Show the desk decisions that changed, all from the frozen preview pair."""
    st.caption(f"Before: {before.get('bundle_id', 'unavailable')} · After: {after.get('bundle_id', 'unavailable')}")
    before_prices = {row["contract_id"]: row for row in before.get("prices", [])}
    price_rows = []
    for row in after.get("prices", []):
        first = before_prices.get(row["contract_id"], {}).get("model_price")
        second = row.get("model_price")
        if first is not None and second is not None and (row.get("position") or abs(second - first) > 1e-10):
            price_rows.append({"Contract": row["contract_id"], "Before · USD/bbl": first, "After · USD/bbl": second, "Change · USD/bbl": second-first})
    render_payload("Price changes · same frozen market and portfolio", price_rows)

    old_quotes = {row["contract_id"]: row for row in before.get("quotes", [])}
    quote_rows = []
    explanation_rows = []
    for row in after.get("quotes", []):
        old = old_quotes.get(row["contract_id"], {})
        if row.get("initial_position_breach"):
            st.warning(f"{row['contract_id']}: initial position is outside its limit. Minimum complete compliant clip after this change: buy {row.get('minimum_compliant_bid_clip', 0)}, sell {row.get('minimum_compliant_ask_clip', 0)}. Zero means no compliant clip; a smaller partial fill is not a compliant repair.")
            st.caption(row.get("position_policy", ""))
        quote_rows.append({
            "Contract": row["contract_id"],
            "Bid before": old.get("bid"), "Bid after": row.get("bid"),
            "Ask before": old.get("ask"), "Ask after": row.get("ask"),
            "Bid size before": old.get("bid_size"), "Bid size after": row.get("bid_size"),
            "Ask size before": old.get("ask_size"), "Ask size after": row.get("ask_size"),
            "Status": row.get("status"),
        })
        for field, label in [
            ("inventory", "Inventory · lots"),
            ("current_stress_risk", "Worst absolute stress P&L · USD"),
            ("bid_marginal_penalty", "Buy-side marginal penalty · USD"),
            ("ask_marginal_penalty", "Sell-side marginal penalty · USD"),
            ("cost_source", "Crossing cost assumption"),
        ]:
            first, second = old.get(field), row.get(field)
            explanation_rows.append({"Contract": row["contract_id"], "Input": label, "Before": f"{first:,.4f}" if isinstance(first, (int, float)) else str(first or "Unavailable"), "After": f"{second:,.4f}" if isinstance(second, (int, float)) else str(second or "Unavailable")})
        explanation_rows.append({"Contract": row["contract_id"], "Input": "Suppression reasons", "Before": "; ".join(old.get("reasons", [])) or "None", "After": "; ".join(row.get("reasons", [])) or "None"})
    render_payload("Quote changes · USD/bbl and integer sizes", quote_rows)
    st.caption("The fair value shifts with volatility. Each side then includes crossing cost and a penalty for its exact one-lot change in inventory stress risk. Sizes obey position and risk limits; suppression reasons take precedence.")
    with st.expander("Why the quote changed", expanded=True):
        render_payload("Inventory, stress, and cost inputs", explanation_rows)

    hedge_rows, cost_rows = [], []
    for key, name in [("delta", "Futures-only"), ("proxy", "Proxy")]:
        old = before.get("hedges", {}).get(key, {})
        new = after.get("hedges", {}).get(key, {})
        old_trades = {trade["contract_id"]: trade["quantity"] for trade in old.get("trades", [])}
        new_trades = {trade["contract_id"]: trade["quantity"] for trade in new.get("trades", [])}
        for contract in sorted(set(old_trades) | set(new_trades)):
            first, second = old_trades.get(contract, 0), new_trades.get(contract, 0)
            hedge_rows.append({"Strategy": name, "Contract": contract, "Lots before": first, "Lots after": second, "Lot change": second-first})
        first_cost, second_cost = old.get("cost"), new.get("cost")
        cost_rows.append({
            "Strategy": name, "Legs before": sum(bool(qty) for qty in old_trades.values()), "Legs after": sum(bool(qty) for qty in new_trades.values()),
            "Total lots before": sum(abs(qty) for qty in old_trades.values()), "Total lots after": sum(abs(qty) for qty in new_trades.values()),
            "Cost before · USD": first_cost, "Cost after · USD": second_cost,
            "Cost change · USD": second_cost-first_cost if first_cost is not None and second_cost is not None else None,
            "Status before": old.get("status"), "Status after": new.get("status"),
        })
    render_payload("Hedge changes · joined by contract", hedge_rows)
    render_payload("Trade count and cost changes", cost_rows)
    st.caption("Lot changes compare proposed hedge portfolios; they are not routed orders or a turnover-cost estimate.")


def render_volatility(desk: Any) -> None:
    st.markdown("### Volatility manager")
    st.caption("Lognormal: annualized %. Normal: USD/bbl / √year. Edits enter Draft before activation.")
    left, right = st.columns([3, 1])
    with left:
        st.write(f"Mode: **{desk.mode.replace('_', ' ')}**")
    with right:
        if st.button("Follow market", key="follow_market", disabled=desk.mode == "follow_market"):
            run_action(desk.follow_market, "Active volatility now follows the market calibration.")
            st.session_state.pop("preview_generation", None)
            st.rerun()
    market_tab, draft_tab, active_tab = st.tabs(["Market", "Draft", "Active"])
    with market_tab:
        st.caption(desk.market_vol.version_id)
        volatility_table(desk.market_vol)
    with draft_tab:
        st.caption(desk.draft_vol.version_id)
        with st.expander("Import volatility nodes into Draft"):
            st.caption("JSON: a VolVersion or array of nodes. CSV: VolNode fields, with underlyings as a JSON array. Values use explicit base units: decimal_annual for lognormal; usd_per_bbl_sqrt_year for normal.")
            uploaded_nodes = st.file_uploader("Volatility nodes · JSON or CSV", type=["json", "csv"], key="vol_upload")
            if st.button("Stage imported volatility", disabled=uploaded_nodes is None, key="stage_vol_upload"):
                run_action(lambda: desk.set_draft(parse_volatility_upload(uploaded_nodes.getvalue(), uploaded_nodes.name)), "Imported nodes staged. Preview validates units, coverage, and price diagnostics.")
                st.session_state.pop("preview_generation", None)
                st.rerun()
        normal_tab, lognormal_tab = st.tabs(["CSO · normal", "Vanilla · lognormal"])
        for model, container in [("normal", normal_tab), ("lognormal", lognormal_tab)]:
            with container:
                st.caption("Normal volatility · USD/bbl / √year" if model == "normal" else "Lognormal volatility · annualized %")
                rows = [row for row in volatility_rows(desk.draft_vol) if row["Model"] == model]
                if not rows:
                    st.caption("No nodes of this model type in the current draft.")
                    continue
                with st.form(f"draft_form_{model}_{desk.draft_vol.version_id}"):
                    edited = st.data_editor(
                        pd.DataFrame(rows),
                        disabled=["Node", "Product", "Underlyings", "Expiry", "Strike", "Model", "Unit", "Origin", "Source", "As of"],
                        column_config={"Volatility": st.column_config.NumberColumn("Volatility", format="%.5f", required=True)},
                        hide_index=True, use_container_width=True,
                        key=f"node_editor_{model}_{desk.draft_vol.version_id}",
                    )
                    if st.form_submit_button(f"Stage {model} node edits"):
                        try:
                            by_id = edited.set_index("Node")["Volatility"].to_dict()
                            nodes = tuple(replace(node, value=float(by_id[node.node_id]) / (100 if node.model == "lognormal" else 1), origin="manual") if node.node_id in by_id else node for node in desk.draft_vol.nodes)
                            run_action(lambda: desk.set_draft(nodes), "Node edits staged. Preview before applying.")
                        except (ValueError, TypeError) as exc:
                            st.session_state["desk_notice"] = ("error", str(exc))
                        st.session_state.pop("preview_generation", None)
                        st.rerun()
        slices = sorted({node.slice_key for node in desk.draft_vol.nodes}, key=str)
        if slices:
            with st.form("shift_form"):
                selected = st.selectbox("Shift slice", slices, format_func=lambda key: f"{key[0]} · {' / '.join(key[1])} · {key[2][:10]} · {key[3]}", key="shift_slice")
                amount = st.number_input("Parallel shift (normal: USD/bbl/√year; lognormal: percentage points)", value=0.0, step=0.25, format="%.4f", key="shift_amount")
                if st.form_submit_button("Stage parallel shift"):
                    run_action(lambda: desk.shift_slice(selected, amount), "Slice shift staged. Preview before applying.")
                    st.session_state.pop("preview_generation", None)
                    st.rerun()
        buttons = st.columns(3)
        if buttons[0].button("Revert draft", key="reset_draft"):
            run_action(desk.reset_draft, "Draft restored from Active.")
            st.session_state.pop("preview_generation", None)
            st.rerun()
        if buttons[1].button("Preview change", key="preview_change", type="primary"):
            if run_action(desk.preview, "Preview computed; Active is unchanged."):
                st.session_state["preview_generation"] = desk.generation
            st.rerun()
        expected = st.session_state.get("preview_generation")
        stale_preview = bool(desk.preview_bundle and desk.preview_bundle.get("current_market_changed", False))
        if stale_preview:
            st.warning("The market changed after this draft began. Revert Draft to current Active and stage the change again before applying.")
        if buttons[2].button("Apply preview", key="apply_preview", disabled=expected is None or expected != desk.generation or stale_preview):
            run_action(lambda: desk.apply(expected_generation=expected), "Preview applied as one active bundle.")
            st.session_state.pop("preview_generation", None)
            st.rerun()
    with active_tab:
        st.caption(desk.active_vol.version_id)
        volatility_table(desk.active_vol)
        history = list(desk.history)
        if history:
            selected_version = st.selectbox("Saved active version", history, key="restore_version")
            if st.button("Restore selected version", key="restore_active", disabled=selected_version == desk.active_vol.version_id):
                run_action(lambda: desk.restore(selected_version), "Selected volatility version restored and revalued.")
                st.session_state.pop("preview_generation", None)
                st.rerun()
    volatility_charts({"Market": desk.market_vol, "Draft": desk.draft_vol, "Active": desk.active_vol})
    if desk.preview_bundle:
        with st.expander("Preview comparison", expanded=True):
            preview = desk.preview_bundle
            if "after" in preview:
                render_preview_comparison(preview.get("before", {}), preview["after"])
                render_result(preview["after"], preview=True)
            else:
                render_result(preview, preview=True)


def install_source(kind: str, payload: Any) -> None:
    from options_lab.adapters import engineering_fixture, parse_csv, parse_json
    from options_lab.controller import DeskController

    if kind == "session":
        from options_lab.session_io import import_session
        restored = import_session(payload.getvalue())
        updates = {"desk": restored.desk, "replay_snapshots": restored.replay_snapshots,
                   "replay_index": restored.replay_index, "replay_playing": False,
                   "replay_failures": list(restored.replay_failures),
                   "import_warnings": restored.import_warnings, "source_sha256": restored.source_sha256,
                   "fixture_selected": restored.fixture_selected, "restored_session_sha256": restored.session_sha256}
        # Parsing, reference checks and fresh evaluations finish before any UI state
        # changes. No provider credentials, connections or autoplay are restored.
        st.session_state.update(updates)
        for key in ["preview_generation", "shift_slice", "vol_slice", "restore_version", "shift_amount", "replay_tick"]:
            st.session_state.pop(key, None)
        if restored.desk.preview_bundle and not restored.desk.preview_bundle.get("current_market_changed"):
            st.session_state["preview_generation"] = restored.desk.generation
        return
    if kind == "fixture":
        imported = engineering_fixture()
    else:
        data = payload.getvalue()
        imported = parse_csv(data) if payload.name.lower().endswith(".csv") else parse_json(data)
    if not imported.snapshots:
        raise ValueError("The input contains no snapshots.")
    first = imported.snapshots[0]
    targets = [contract.contract_id for contract in first.contracts if contract.kind == "cso"]
    if not targets:
        raise ValueError("A CSO contract with its identified underlying legs is required.")
    settings = imported.settings or Settings(target_id=targets[0])
    desk = DeskController(first, imported.portfolio, settings)
    # Commit source state only after construction/calibration/evaluation succeeds.
    st.session_state["desk"] = desk
    st.session_state["replay_snapshots"] = imported.snapshots
    st.session_state["replay_index"] = 0
    st.session_state["replay_playing"] = False
    st.session_state["replay_failures"] = []
    st.session_state["import_warnings"] = imported.warnings
    st.session_state["source_sha256"] = imported.source_sha256
    st.session_state["fixture_selected"] = kind == "fixture" or any(
        word in f"{first.mode} {first.source}".lower() for word in ["engineering", "fixture", "synthetic"]
    )
    st.session_state.pop("preview_generation", None)
    st.session_state.pop("restored_session_sha256", None)


def advance_replay() -> bool:
    snapshots = st.session_state.get("replay_snapshots", ())
    current = st.session_state.get("replay_index", 0)
    if current + 1 >= len(snapshots):
        st.session_state["replay_playing"] = False
        return False
    desk = st.session_state["desk"]
    # The replay cursor tracks consumed records, including a rejected/failed record.
    # Retrying that record after refresh has advanced the market would deadlock on
    # the duplicate-update guard. Pause to expose the failure; next Step may recover.
    try:
        desk.refresh(snapshots[current + 1])
        succeeded = True
        st.session_state["desk_notice"] = ("success", "Local replay advanced; the active bundle was recomputed.")
    except Exception as exc:
        succeeded = False
        st.session_state["desk_notice"] = ("error", str(exc))
    st.session_state["replay_index"] = current + 1
    st.session_state.pop("preview_generation", None)
    if succeeded:
        if current + 2 >= len(snapshots):
            st.session_state["replay_playing"] = False
        return True
    error = st.session_state["desk_notice"][1]
    st.session_state.setdefault("replay_failures", []).append({"index":current+1,"snapshot_id":snapshots[current+1].snapshot_id,"error":error})
    st.session_state["desk_notice"] = ("error", f"Replay record {current + 2} failed and was consumed: {error}. Step or Play continues with the next record.")
    st.session_state["replay_playing"] = False
    return False


@st.fragment(run_every=1.0)
def replay_controls() -> None:
    snapshots = st.session_state.get("replay_snapshots", ())
    current = st.session_state.get("replay_index", 0)
    playing = st.session_state.get("replay_playing", False)
    complete = current + 1 >= len(snapshots)
    left, play, pause, step = st.columns([5, 1, 1, 1])
    left.caption(f"Local replay · observation {current + 1} of {len(snapshots)} · {'playing' if playing else 'paused'}")
    if play.button("Play", disabled=playing or complete, key="replay_play"):
        st.session_state["replay_playing"] = True
        st.session_state["replay_tick"] = time.monotonic()
        st.rerun()
    if pause.button("Pause", disabled=not playing, key="replay_pause"):
        st.session_state["replay_playing"] = False
        st.rerun()
    if step.button("Step", disabled=playing or complete, key="replay_step"):
        advance_replay()
        st.rerun()
    if playing and time.monotonic() - st.session_state.get("replay_tick", 0) >= 1.0:
        st.session_state["replay_tick"] = time.monotonic()
        advance_replay()
        st.rerun()


def portfolio_controls(desk: Any) -> None:
    with st.expander("Portfolio, quote, and hedge assumptions"):
        targets = [contract.contract_id for contract in desk.market.contracts if contract.kind == "cso"]
        with st.form(f"inputs_{desk.settings.settings_id}"):
            first, second = st.columns(2)
            target = first.selectbox("Target CSO", targets, index=targets.index(desk.settings.target_id) if desk.settings.target_id in targets else 0)
            current_lots = next((position.quantity for position in desk.portfolio if position.contract_id == target), 0)
            lots = second.number_input("Target position · integer contracts", value=current_lots, step=1)
            cols = st.columns(3)
            future_bound = cols[0].number_input("Futures hedge bound", min_value=0, value=desk.settings.future_bound, step=1)
            option_bound = cols[1].number_input("Option hedge bound", min_value=0, value=desk.settings.option_bound, step=1)
            risk_limit = cols[2].number_input("Risk limit · USD", min_value=0.0, value=float(desk.settings.risk_limit_dollars), step=1000.0)
            cols = st.columns(3)
            fee = cols[0].number_input("Fee per contract · USD", min_value=0.0, value=float(desk.settings.fee_per_contract), step=0.25)
            confirmed = cols[1].checkbox("Fee assumption confirmed", value=desk.settings.fee_confirmed)
            allow_cso = cols[2].checkbox("Allow CSO hedge instruments", value=desk.settings.allow_cso_hedge)
            st.caption("Fee confirmation is a user-supplied cost assumption. It does not establish a live or executable market.")
            if st.form_submit_button("Recompute assumptions"):
                portfolio = tuple(position for position in desk.portfolio if position.contract_id != target) + (Position(target, int(lots)),)
                settings = replace(desk.settings, target_id=target, future_bound=int(future_bound), option_bound=int(option_bound), risk_limit_dollars=float(risk_limit), fee_per_contract=float(fee), fee_confirmed=confirmed, allow_cso_hedge=allow_cso)
                run_action(lambda: desk.set_inputs(portfolio, settings), "Portfolio and assumptions revalued in one bundle.")
                st.session_state.pop("preview_generation", None)
                st.rerun()
        st.caption("Other imported positions remain in the portfolio.")
        render_payload("Current portfolio", desk.portfolio)
        with st.expander("All settings"):
            st.json(plain(desk.settings))


def loaded_desk(desk: Any) -> None:
    if st.session_state.get("fixture_selected", False):
        st.warning("SYNTHETIC DEMO · Engineering fixture – not market data. Model quotes and hypothetical risk only.")
    mode_label = "Manual volatility · locked" if desk.mode == "manual" else "Following market calibration"
    html(f'<div class="az-source-strip"><span>SNAPSHOT <strong>{escape(desk.market.as_of)}</strong></span>'
         f'<span>VOLATILITY <strong>{mode_label}</strong></span>'
         '<span>EXECUTION <strong>Not connected</strong></span></div>')
    if not desk.market.feed_alive:
        st.error("Source reports feed unavailable. Inspect quote suppression and the last valid bundle.")
    if desk.error:
        st.error(f"Update failed: {desk.error}. Inspect the current calculation status below.")
    for warning in st.session_state.get("import_warnings", ()):
        if st.session_state.get("fixture_selected", False) and warning == "SYNTHETIC ENGINEERING FIXTURE — not observed WTI prices or real-data calibration.":
            continue  # The persistent synthetic banner already carries this exact source notice.
        st.warning(str(warning))
    overview_tab, vol_tab, desk_tab = st.tabs(["Desk overview", "Volatility workspace", "Valuation & risk"])
    with overview_tab:
        overview(desk)
    with vol_tab:
        render_volatility(desk)
    with desk_tab:
        render_result(desk.bundle)
    st.markdown('<div class="desk-rule"></div>', unsafe_allow_html=True)
    replay_controls()
    if st.session_state.get("replay_failures"):
        with st.expander("Replay failure log"):
            render_payload("Consumed records with errors", st.session_state["replay_failures"])
    with st.expander("Source provenance and imported observations"):
        st.text(f"Source: {desk.market.source or 'not supplied'}")
        st.text(f"Data mode: {desk.market.mode}")
        for warning in st.session_state.get("import_warnings", ()):
            st.caption(str(warning))
        st.text(f"Snapshot: {desk.market.snapshot_id}")
        st.text(f"Import SHA-256: {st.session_state.get('source_sha256', 'unavailable')}")
        if st.session_state.get("restored_session_sha256"):
            st.text(f"Restored session SHA-256: {st.session_state['restored_session_sha256']}")
        st.caption(f"Received: {desk.market.received_at or 'not supplied'} · sequence {desk.market.sequence}")
        render_payload("Contracts", desk.market.contracts)
        render_payload("Observed quotes or settlements", desk.market.quotes)
    portfolio_controls(desk)


def main() -> None:
    st.set_page_config(page_title="WTI Options Desk | Antony Zuo", page_icon=str(Path(__file__).parent / "assets" / "az-mark.svg"), layout="wide", initial_sidebar_state="collapsed")
    st.set_option("client.toolbarMode", "minimal")
    st.markdown(DESK_CSS, unsafe_allow_html=True)
    masthead(loaded="desk" in st.session_state)
    if "pending_source_mode" in st.session_state:
        st.session_state["source_mode"] = st.session_state.pop("pending_source_mode")
    action = source_panel()
    if action:
        if run_action(lambda: install_source(*action), "Local source loaded and validated."):
            # Render the complete sidebar against the newly installed state.
            st.rerun()
    notice = st.session_state.get("desk_notice")
    if notice:
        if notice[0] == "success":
            st.toast(notice[1])
            st.session_state.pop("desk_notice", None)
        else:
            getattr(st, notice[0])(notice[1])
    if "desk" in st.session_state:
        loaded_desk(st.session_state["desk"])
        if st.sidebar.button("Clear local session", key="clear_session"):
            for key in ["desk", "replay_snapshots", "replay_index", "replay_playing", "replay_failures", "fixture_selected", "preview_generation", "desk_notice", "import_warnings", "source_sha256", "restored_session_sha256"]:
                st.session_state.pop(key, None)
            st.rerun()
    else:
        onboarding()
    footer()


if __name__ == "__main__":
    main()
