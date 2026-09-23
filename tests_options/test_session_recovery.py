"""Regression tests for bad-record recovery and portable desk sessions."""
from dataclasses import replace
import json
import subprocess
import sys
from hashlib import sha256

import pytest

from options_lab.adapters import engineering_fixture
from options_lab.controller import DeskController
from tests_options.test_app import fixture_app


def make_desk():
    imported = engineering_fixture()
    return DeskController(imported.snapshots[0], imported.portfolio, imported.settings), imported


def missing_option_quotes(snapshot):
    return replace(snapshot, quotes=tuple(q for q in snapshot.quotes if snapshot.contract_map[q.contract_id].kind == "future"))


def test_failed_replay_consumes_bad_record_and_next_step_recovers():
    app = fixture_app()
    original = app.session_state["replay_snapshots"]
    app.session_state["replay_snapshots"] = (original[0], missing_option_quotes(original[1]), original[2])
    app.button(key="replay_step").click().run(timeout=30)
    desk = app.session_state["desk"]
    assert not app.exception
    assert desk.bundle["status"] == "failed"
    assert not desk.bundle["quotes"] and not desk.bundle["hedges"]
    assert desk.preview_bundle is None
    assert app.session_state["replay_index"] == 1
    assert not app.session_state["replay_playing"]
    assert app.session_state["replay_failures"][0]["index"] == 1
    app.button(key="replay_step").click().run(timeout=30)
    assert not app.exception
    assert app.session_state["replay_index"] == 2
    assert desk.market.snapshot_id == original[2].snapshot_id
    assert desk.bundle["snapshot_id"] == original[2].snapshot_id
    assert desk.bundle["status"] != "failed"


def test_calibration_exception_clears_proposals_and_preview_then_recovers(monkeypatch):
    import options_lab.controller as module
    desk, imported = make_desk()
    desk.shift_slice(desk.active_vol.nodes[0].slice_key, 0.1)
    desk.preview()
    real_calibrate = module.calibrate_market
    def fail_middle(snapshot):
        if snapshot.sequence == imported.snapshots[1].sequence:
            raise ValueError("Bad calibration record")
        return real_calibrate(snapshot)
    monkeypatch.setattr(module, "calibrate_market", fail_middle)
    with pytest.raises(ValueError, match="Bad calibration"):
        desk.refresh(imported.snapshots[1])
    assert desk.market == imported.snapshots[1]
    assert desk.bundle["status"] == "failed"
    assert not desk.bundle["quotes"] and not desk.bundle["hedges"]
    assert desk.preview_bundle is None
    desk.refresh(imported.snapshots[2])
    assert desk.bundle["snapshot_id"] == imported.snapshots[2].snapshot_id


def test_session_round_trip_preserves_frozen_draft_history_and_replay(tmp_path):
    from options_lab.session_io import export_session, import_session
    desk, imported = make_desk()
    desk.shift_slice(desk.active_vol.nodes[0].slice_key, 0.25)
    desk.apply()
    desk.shift_slice(desk.active_vol.nodes[0].slice_key, 0.1)
    desk.refresh(imported.snapshots[1])
    preview = desk.preview()
    assert preview["current_market_changed"]
    data = export_session(desk, replay_snapshots=imported.snapshots, replay_index=1,
                          source_sha256=imported.source_sha256, import_warnings=imported.warnings,
                          fixture_selected=True)
    restored = import_session(data)
    assert restored.desk is not desk
    assert restored.desk.bundle == desk.bundle
    assert restored.desk.market_vol == desk.market_vol
    assert restored.desk.active_vol == desk.active_vol
    assert restored.desk.draft_vol == desk.draft_vol
    assert restored.desk.history == desk.history
    assert restored.desk.preview() == preview
    assert restored.replay_index == 1
    assert restored.replay_snapshots == imported.snapshots
    assert restored.source_sha256 == imported.source_sha256
    path = tmp_path / "session.json"
    path.write_bytes(data)
    program = "from options_lab.session_io import import_session; from pathlib import Path; import sys,json; s=import_session(Path(sys.argv[1]).read_bytes()); print(json.dumps(s.desk.bundle,sort_keys=True))"
    output = subprocess.check_output([sys.executable, "-c", program, str(path)], text=True)
    assert json.loads(output) == desk.bundle
    first_version = next(iter(restored.desk.history))
    restored.desk.restore(first_version)
    assert restored.desk.active_vol.version_id == first_version
    assert restored.desk.bundle["snapshot_id"] == desk.market.snapshot_id


def test_session_rejects_tampering_without_changing_existing_desk():
    from options_lab.session_io import export_session, import_session
    desk, _ = make_desk()
    original = desk.bundle
    payload = json.loads(export_session(desk))
    payload["state"]["settings"]["fee_per_contract"] += 1
    with pytest.raises(ValueError, match="hash"):
        import_session(payload)
    assert desk.bundle == original


def test_failed_session_round_trip_keeps_empty_advice_and_can_recover():
    from options_lab.session_io import export_session, import_session
    desk, imported = make_desk()
    bad = missing_option_quotes(imported.snapshots[1])
    with pytest.raises(ValueError):
        desk.refresh(bad)
    restored = import_session(export_session(desk, replay_snapshots=(imported.snapshots[0], bad, imported.snapshots[2]), replay_index=1))
    assert restored.desk.bundle["status"] == "failed"
    assert not restored.desk.bundle["quotes"] and not restored.desk.bundle["hedges"]
    restored.desk.refresh(imported.snapshots[2])
    assert restored.desk.bundle["status"] != "failed"


def rehash(payload):
    value = {key: value for key, value in payload.items() if key != "content_sha256"}
    payload["content_sha256"] = sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return payload


@pytest.mark.parametrize("corruption", ["version", "result", "cursor", "follow", "future_basis", "fixture_boolean"])
def test_rehashed_inconsistent_sessions_are_rejected(corruption):
    from options_lab.session_io import export_session, import_session
    desk, _ = make_desk()
    if corruption == "follow":
        desk.shift_slice(desk.active_vol.nodes[0].slice_key, 0.1)
        desk.apply()
    payload = json.loads(export_session(desk))
    if corruption == "version":
        payload["state"]["history"]["vol-invalid"] = payload["state"]["history"].pop(next(iter(payload["state"]["history"])))
    elif corruption == "result":
        payload["state"]["active_result"]["quotes"][0]["bid"] = 999
    elif corruption == "cursor":
        payload["replay"]["index"] = 9
    elif corruption == "follow":
        payload["state"]["mode"] = "follow_market"
    elif corruption == "future_basis":
        payload["state"]["draft_basis"]["market"]["as_of"] = "2030-01-01T00:00:00+00:00"
    else:
        payload["provenance"]["fixture_selected"] = "false"
    with pytest.raises(ValueError):
        import_session(rehash(payload))


def test_invalid_staged_draft_is_preserved_without_activating():
    from options_lab.session_io import export_session, import_session
    desk, _ = make_desk()
    original = desk.bundle
    desk.set_draft(tuple(replace(n, value=-1) for n in desk.draft_vol.nodes))
    with pytest.raises(ValueError):
        desk.preview()
    restored = import_session(export_session(desk)).desk
    assert restored.bundle == original
    assert restored.draft_vol == desk.draft_vol
    assert restored.preview_bundle is None
    with pytest.raises(ValueError):
        restored.apply()
    assert restored.bundle == original


def test_ui_session_install_is_transactional_and_always_paused(monkeypatch):
    from options_lab import app as module
    from options_lab.session_io import export_session
    desk, imported = make_desk()
    new_desk, _ = make_desk()
    new_desk.shift_slice(new_desk.active_vol.nodes[0].slice_key, 0.25)
    new_desk.apply()
    state = {"desk": desk, "replay_snapshots": imported.snapshots, "replay_index": 0, "replay_playing": True}
    monkeypatch.setattr(module.st, "session_state", state)
    class Upload:
        def __init__(self, data): self.data = data
        def getvalue(self): return self.data
    with pytest.raises(ValueError):
        module.install_source("session", Upload(b'{"schema": "bad"}'))
    assert state["desk"] is desk and state["replay_playing"]
    module.install_source("session", Upload(export_session(new_desk, replay_snapshots=imported.snapshots)))
    assert state["desk"].bundle == new_desk.bundle
    assert state["desk"] is not desk
    assert not state["replay_playing"]
    assert state["fixture_selected"]


def test_duplicate_replay_is_consumed_and_later_good_record_reachable():
    app = fixture_app()
    snapshots = app.session_state["replay_snapshots"]
    app.session_state["replay_snapshots"] = (snapshots[0], snapshots[0], snapshots[2])
    app.button(key="replay_step").click().run(timeout=30)
    assert app.session_state["replay_index"] == 1
    assert "duplicate" in app.session_state["replay_failures"][0]["error"]
    app.button(key="replay_step").click().run(timeout=30)
    assert app.session_state["replay_index"] == 2
    assert app.session_state["desk"].market == snapshots[2]
    assert not app.exception


@pytest.mark.parametrize("location", ["current", "history", "draft_basis", "replay"])
def test_session_checks_snapshot_semantics_in_every_context(location):
    from options_lab.session_io import export_session, import_session
    desk, _ = make_desk()
    payload = json.loads(export_session(desk))
    if location == "current":
        snapshot = payload["state"]["market"]
    elif location == "history":
        snapshot = next(iter(payload["state"]["history"].values()))["market"]
    elif location == "draft_basis":
        snapshot = payload["state"]["draft_basis"]["market"]
    else:
        snapshot = payload["replay"]["snapshots"][0]
    snapshot["contracts"][0]["currency"] = "EUR"
    with pytest.raises(ValueError, match="USD currency"):
        import_session(rehash(payload))


def test_session_import_size_limit():
    from options_lab.session_io import import_session
    with pytest.raises(ValueError, match="20 MB"):
        import_session(b" " * 20_000_001)


def test_transient_calibration_failure_session_is_explicitly_rejected(monkeypatch):
    import options_lab.controller as module
    from options_lab.session_io import export_session, import_session
    desk, imported = make_desk()
    with monkeypatch.context() as patch:
        patch.setattr(module, "calibrate_market", lambda _: (_ for _ in ()).throw(ValueError("Transient calibration failure")))
        with pytest.raises(ValueError, match="Transient"):
            desk.refresh(imported.snapshots[1])
    data = export_session(desk, replay_snapshots=imported.snapshots, replay_index=1)
    with pytest.raises(ValueError, match="Market volatility does not match"):
        import_session(data)
    assert desk.bundle["status"] == "failed"
    assert not desk.bundle["quotes"] and not desk.bundle["hedges"]


def test_ui_explains_minimum_complete_clip_and_infeasible_hedge():
    app = fixture_app()
    desk = app.session_state["desk"]
    desk.set_inputs(tuple(replace(p, quantity=-50) for p in desk.portfolio),
                    replace(desk.settings, clip_size=10, risk_limit_dollars=1e9))
    app.run(timeout=30)
    assert not app.exception
    assert any("Minimum complete compliant clip: buy 10" in warning.value for warning in app.warning)
    assert any("No compliant hedge proposal" in error.value for error in app.error)
