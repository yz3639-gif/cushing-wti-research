"""User-visible state transitions, without a feed or network connection."""

from pathlib import Path
from dataclasses import replace
import json

from streamlit.testing.v1 import AppTest


APP = Path(__file__).resolve().parents[1] / "options_lab" / "app.py"
FIXTURE_LABEL = "Engineering fixture – not market data"


def open_app():
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    return app


def fixture_app():
    app = open_app()
    app.radio(key="source_mode").set_value(FIXTURE_LABEL).run()
    app.button(key="load_fixture").click().run(timeout=30)
    assert not app.exception
    assert not app.error, [error.value for error in app.error]
    return app


def stage_shift(app, amount=0.1):
    app.number_input(key="shift_amount").set_value(amount)
    next(button for button in app.button if button.label == "Stage parallel shift").click().run(timeout=30)
    assert not app.exception


def test_default_has_no_prices_and_selection_does_not_auto_load_fixture():
    app = open_app()
    assert app.radio(key="source_mode").value == "Authorized local data"
    assert not app.metric
    assert app.button(key="load_local").disabled
    app.radio(key="source_mode").set_value(FIXTURE_LABEL).run()
    assert not app.metric
    assert app.button(key="load_fixture")
    assert any(FIXTURE_LABEL in warning.value for warning in app.warning)


def test_full_session_download_appears_immediately_after_loading():
    app = fixture_app()
    assert any(button.label == "Download full session" for button in app.get("download_button"))


def test_preview_and_apply_keep_outputs_in_one_versioned_bundle():
    app = fixture_app()
    desk = app.session_state["desk"]
    original_bundle = desk.bundle["bundle_id"]
    original_vol = desk.active_vol.version_id
    stage_shift(app)
    assert desk.active_vol.version_id == original_vol
    assert desk.bundle["bundle_id"] == original_bundle
    assert app.button(key="apply_preview").disabled
    app.button(key="preview_change").click().run(timeout=30)
    assert not app.exception
    assert desk.bundle["bundle_id"] == original_bundle
    preview_id = desk.preview_bundle["after"]["bundle_id"]
    assert not app.button(key="apply_preview").disabled
    displayed = [table.value for table in app.dataframe]
    quote_comparison = next(table for table in displayed if "Bid before" in table.columns)
    quoted_contract = desk.preview_bundle["after"]["quotes"][0]
    shown_quote = quote_comparison.set_index("Contract").loc[quoted_contract["contract_id"]]
    assert shown_quote["Bid after"] == quoted_contract["bid"]
    assert shown_quote["Ask size after"] == quoted_contract["ask_size"]
    assert any("Lot change" in table.columns for table in displayed)
    assert any("Cost change · USD" in table.columns for table in displayed)
    app.button(key="apply_preview").click().run(timeout=30)
    assert not app.exception
    assert desk.bundle["bundle_id"] == preview_id
    assert desk.active_vol.version_id != original_vol
    assert desk.bundle["vol_version_id"] == desk.active_vol.version_id
    assert desk.bundle["snapshot_id"] == desk.market.snapshot_id
    assert desk.mode == "manual"
    assert any(FIXTURE_LABEL in warning.value for warning in app.warning)


def test_invalid_draft_cannot_replace_active_result():
    app = fixture_app()
    desk = app.session_state["desk"]
    original = desk.bundle["bundle_id"]
    stage_shift(app, -1000.0)
    app.button(key="preview_change").click().run(timeout=30)
    assert not app.exception
    assert desk.bundle["bundle_id"] == original
    assert desk.preview_bundle is None
    assert app.button(key="apply_preview").disabled
    assert app.error
    app.button(key="reset_draft").click().run(timeout=30)
    assert not app.exception
    assert desk.draft_vol.version_id == desk.active_vol.version_id


def test_replay_advances_market_and_follow_mode_restores_calibration():
    app = fixture_app()
    desk = app.session_state["desk"]
    stage_shift(app)
    app.button(key="preview_change").click().run(timeout=30)
    app.button(key="apply_preview").click().run(timeout=30)
    manual = desk.active_vol.version_id
    old_snapshot = desk.market.snapshot_id
    app.button(key="replay_step").click().run(timeout=30)
    assert not app.exception
    assert desk.market.snapshot_id != old_snapshot
    assert desk.active_vol.version_id == manual
    assert desk.bundle["snapshot_id"] == desk.market.snapshot_id
    app.button(key="follow_market").click().run(timeout=30)
    assert not app.exception
    assert desk.mode == "follow_market"
    assert desk.active_vol.version_id == desk.market_vol.version_id
    assert desk.bundle["vol_version_id"] == desk.market_vol.version_id


def test_live_panel_never_claims_a_connection():
    app = open_app()
    app.radio(key="source_mode").set_value("Live connection").run()
    assert not app.exception
    assert any("Connection pending" in notice.value for notice in app.info)
    assert not app.metric


def test_volatility_import_preserves_explicit_model_units():
    from options_lab.ui import parse_volatility_upload

    nodes = [
        {"product": "LCE", "underlyings": ["CLM27"], "expiry": "2027-05-14T18:30:00+00:00", "strike": 80.0, "model": "lognormal", "value": 0.32, "unit": "decimal_annual", "as_of": "2026-09-22T14:00:00+00:00", "source": "User test"},
        {"product": "B7A", "underlyings": ["CLM27", "CLN27"], "expiry": "2027-05-14T18:30:00+00:00", "strike": 1.0, "model": "normal", "value": 3.2, "unit": "usd_per_bbl_sqrt_year", "as_of": "2026-09-22T14:00:00+00:00", "source": "User test"},
    ]
    imported = parse_volatility_upload(json.dumps({"nodes": nodes}).encode(), "vol.json")
    assert imported[0].value == 0.32
    assert imported[0].unit == "decimal_annual"
    assert imported[1].value == 3.2
    assert imported[1].unit == "usd_per_bbl_sqrt_year"
    assert imported[1].underlyings == ("CLM27", "CLN27")


def test_demo_entry_explicitly_loads_fixture_and_preserves_source_label():
    app = open_app()
    assert "desk" not in app.session_state
    app.button(key="explore_demo").click().run(timeout=30)
    assert not app.exception
    assert app.radio(key="source_mode").value == FIXTURE_LABEL
    assert app.session_state["fixture_selected"] is True
    assert app.session_state["desk"].market.mode == "engineering_fixture"


def test_overview_does_not_show_suppressed_hedge_baselines_as_usable_results():
    app = fixture_app()
    desk = app.session_state["desk"]
    desk.refresh(replace(desk.market, feed_alive=False, sequence=desk.market.sequence + 1))
    app.run(timeout=30)
    assert not app.exception
    cards = {metric.label: metric.value for metric in app.metric}
    assert cards["Proxy hedge · estimated cost"] == "Unavailable"
    assert cards["Proxy hedge · worst scenario loss"] == "Unavailable"
    assert any("Proxy hedge unavailable" in notice.value for notice in app.warning)


def test_overview_resolves_engine_default_cso_when_target_id_is_empty():
    app = fixture_app()
    desk = app.session_state["desk"]
    desk.set_inputs(desk.portfolio, replace(desk.settings, target_id=""))
    app.run(timeout=30)
    assert not app.exception
    expected = next(row for row in desk.bundle["prices"] if row["kind"] == "cso")
    cards = [metric for metric in app.metric if metric.label == "CSO normal vol · $/bbl/√yr"]
    assert cards[0].value == f"{expected['vol']:,.3f}"
    assert not any("No quote available" in notice.value for notice in app.info)


def test_public_demo_keeps_volatility_workflow_without_any_file_uploads():
    app = AppTest.from_file(str(APP.with_name("cloud_app.py"))).run(timeout=30)
    assert not app.exception
    assert not app.get("file_uploader")
    app.button(key="explore_demo").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["fixture_selected"] is True
    assert not app.get("file_uploader")
    desk = app.session_state["desk"]
    original = desk.active_vol.version_id
    stage_shift(app)
    app.button(key="preview_change").click().run(timeout=30)
    app.button(key="apply_preview").click().run(timeout=30)
    assert not app.exception
    assert desk.mode == "manual"
    assert desk.active_vol.version_id != original
    assert not app.get("file_uploader")
