"""Portable, data-only desk sessions with transactional reconstruction.

Imported result JSON is evidence to compare against a fresh evaluation, never an
instruction or a shortcut around pricing/validation. No connections are restored.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import re
from threading import RLock
from typing import Any

from .controller import DeskController
from .adapters import MAX_IMPORT_BYTES, MAX_SNAPSHOTS, validate_snapshot
from .models import ENGINE_VERSION, MarketSnapshot, Position, Settings, VolVersion, utc, portfolio_id
from .volatility import calibrate_market, validate_version

SCHEMA = "wti-options-desk-session"
SCHEMA_VERSION = 1


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(value: Any) -> str:
    return sha256(_canonical(value)).hexdigest()


def _context(market, portfolio, settings):
    return {"market": asdict(market), "portfolio": [asdict(p) for p in portfolio], "settings": asdict(settings)}


@dataclass(frozen=True)
class RestoredSession:
    desk: DeskController
    replay_snapshots: tuple[MarketSnapshot, ...]
    replay_index: int
    replay_failures: tuple[dict, ...]
    source_sha256: str
    import_warnings: tuple[str, ...]
    fixture_selected: bool
    session_sha256: str


def export_session(desk: DeskController, *, replay_snapshots=(), replay_index=0,
                   replay_failures=(), source_sha256="", import_warnings=(),
                   fixture_selected=False) -> bytes:
    """Save full immutable inputs plus computed-result evidence under one hash."""
    with desk._lock:
        basis_market, basis_vol, basis_portfolio, basis_settings = desk._draft_basis
        state = {
            **_context(desk.market, desk.portfolio, desk.settings),
            "mode": desk.mode, "generation": desk.generation, "error": desk.error,
            "market_vol": asdict(desk.market_vol), "draft_vol": asdict(desk.draft_vol),
            "active_vol": asdict(desk.active_vol),
            "history": {key: {"vol": asdict(vol), **_context(*desk._history_inputs[key])}
                        for key, vol in desk.history.items()},
            "draft_basis": {**_context(basis_market, basis_portfolio, basis_settings), "active_vol": asdict(basis_vol)},
            "draft_validation_errors": validate_version(desk.draft_vol, basis_market),
            "active_result": desk.bundle,
            "preview_result": desk.preview_bundle,
            "preview_inputs": None,
        }
        if desk.preview_bundle is not None:
            p_market, p_before, p_portfolio, p_settings, p_after = desk._preview_basis
            state["preview_inputs"] = {**_context(p_market,p_portfolio,p_settings), "before_vol": asdict(p_before), "after_vol": asdict(p_after)}
        snapshots = tuple(replay_snapshots) or (desk.market,)
        envelope = {
            "schema": SCHEMA, "schema_version": SCHEMA_VERSION, "engine_version": ENGINE_VERSION,
            "state": state,
            "replay": {"snapshots": [asdict(s) for s in snapshots], "index": replay_index, "failures": list(replay_failures)},
            "provenance": {"source_sha256": source_sha256, "import_warnings": list(import_warnings), "fixture_selected": fixture_selected},
        }
        envelope["content_sha256"] = _digest(envelope)
        return json.dumps(envelope, indent=2, allow_nan=False, ensure_ascii=False).encode("utf-8")


def _int(value, field, *, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{field} must be an integer >= {minimum}")
    return value


def _read_context(record):
    market = validate_snapshot(MarketSnapshot.from_dict(record["market"]))
    settings = Settings.from_dict(record["settings"])
    portfolio = tuple(Position(**p) for p in record["portfolio"])
    known = market.contract_map
    if settings.target_id and settings.target_id not in known:
        raise ValueError("Session target contract is not in its market")
    if any(p.contract_id not in known for p in portfolio):
        raise ValueError("Session portfolio contains an unknown contract")
    return market, portfolio, settings


def _valid_version(vol, market, label):
    errors = validate_version(vol, market)
    if errors:
        raise ValueError(f"Invalid {label}: {'; '.join(errors)}")


def _same_result(saved, computed, label):
    if _digest(saved) != _digest(computed):
        raise ValueError(f"{label} did not reproduce from the saved inputs; session was not installed. Engine changes or a solver time guard may require a new analysis.")


def import_session(data: bytes | str | dict) -> RestoredSession:
    """Validate and reconstruct a fresh object; never mutate an existing session."""
    try:
        if isinstance(data, (str, bytes)) and len(data if isinstance(data, bytes) else data.encode("utf-8")) > MAX_IMPORT_BYTES:
            raise ValueError("Session import exceeds the 20 MB limit")
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig")
        payload = json.loads(data) if isinstance(data, str) else data
        if not isinstance(payload, dict):
            raise ValueError("Session must be a JSON object")
        if len(_canonical(payload)) > MAX_IMPORT_BYTES:
            raise ValueError("Session import exceeds the 20 MB limit")
        if set(payload) != {"schema", "schema_version", "engine_version", "state", "replay", "provenance", "content_sha256"}:
            raise ValueError("Unsupported session envelope fields")
        if payload["schema"] != SCHEMA or type(payload["schema_version"]) is not int or payload["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported session schema version")
        if payload["engine_version"] != ENGINE_VERSION:
            raise ValueError("Session engine version differs from this engine")
        content_hash = _digest({k: v for k, v in payload.items() if k != "content_sha256"})
        if payload["content_sha256"] != content_hash:
            raise ValueError("Session content hash mismatch")
        state, replay, provenance = payload["state"], payload["replay"], payload["provenance"]
        if set(state) != {"market", "portfolio", "settings", "mode", "generation", "error", "market_vol", "draft_vol", "active_vol", "history", "draft_basis", "draft_validation_errors", "active_result", "preview_result", "preview_inputs"}:
            raise ValueError("Unsupported desk-state fields")
        if set(replay) != {"snapshots", "index", "failures"} or set(provenance) != {"source_sha256", "import_warnings", "fixture_selected"}:
            raise ValueError("Unsupported replay or provenance fields")
        market, portfolio, settings = _read_context(state)
        mode = state["mode"]
        if mode not in ("manual", "follow_market"):
            raise ValueError("Invalid session volatility mode")
        generation = _int(state["generation"], "generation")
        if state["error"] is not None and not isinstance(state["error"], str):
            raise ValueError("Session error must be text or null")
        market_vol, draft_vol, active_vol = (VolVersion.from_dict(state[key]) for key in ("market_vol", "draft_vol", "active_vol"))
        if market_vol != calibrate_market(market):
            raise ValueError("Market volatility does not match its saved observations")
        history, history_inputs = {}, {}
        for key, record in state["history"].items():
            vol = VolVersion.from_dict(record["vol"])
            if key != vol.version_id:
                raise ValueError("History volatility version ID mismatch")
            context = _read_context(record)
            _valid_version(vol, context[0], "history volatility")
            history[key], history_inputs[key] = vol, context
        if active_vol.version_id not in history or history[active_vol.version_id] != active_vol:
            raise ValueError("Active volatility is absent from saved history")
        basis = state["draft_basis"]
        basis_market, basis_portfolio, basis_settings = _read_context(basis)
        basis_vol = VolVersion.from_dict(basis["active_vol"])
        if history.get(basis_vol.version_id) != basis_vol:
            raise ValueError("Frozen draft basis is absent from saved history")
        if utc(basis_market.as_of) > utc(market.as_of):
            raise ValueError("Frozen draft basis is newer than the current market")
        _valid_version(basis_vol, basis_market, "draft basis")
        # An invalid staged edit may be saved, but its diagnostics must agree and
        # it never becomes Active through import. Preview/Apply will still reject it.
        if validate_version(draft_vol, basis_market) != state["draft_validation_errors"]:
            raise ValueError("Draft validation diagnostics do not match")
        if not isinstance(replay["snapshots"], list) or not 1 <= len(replay["snapshots"]) <= MAX_SNAPSHOTS:
            raise ValueError("Session replay must contain 1 to 2000 snapshots")
        snapshots = tuple(validate_snapshot(MarketSnapshot.from_dict(s)) for s in replay["snapshots"])
        index = _int(replay["index"], "replay index")
        if not snapshots or index >= len(snapshots):
            raise ValueError("Replay cursor is outside its saved observations")
        failures = replay["failures"]
        if not isinstance(failures, list):
            raise ValueError("Replay failures must be an array")
        for failure in failures:
            if set(failure) != {"index", "snapshot_id", "error"}:
                raise ValueError("Invalid replay failure record")
            failed_index = _int(failure["index"], "failed replay index")
            if failed_index > index or failure["snapshot_id"] != snapshots[failed_index].snapshot_id or not isinstance(failure["error"], str):
                raise ValueError("Replay failure does not match its observation")
        # A rejected old/duplicate record may be consumed without replacing market.
        # In that case the latest matching prior observation must still exist.
        if not any(s.snapshot_id == market.snapshot_id for s in snapshots[:index+1]):
            raise ValueError("Current market does not match the consumed replay")
        if snapshots[index].snapshot_id != market.snapshot_id and not any(f["index"] == index for f in failures):
            raise ValueError("Replay cursor differs from market without a recorded failure")
        source_hash = provenance["source_sha256"]
        if not isinstance(source_hash, str) or (source_hash and not re.fullmatch(r"[0-9a-f]{64}", source_hash)):
            raise ValueError("Invalid source SHA-256")
        warnings = provenance["import_warnings"]
        if not isinstance(warnings, list) or any(not isinstance(w, str) for w in warnings):
            raise ValueError("Import warnings must be text records")
        if type(provenance["fixture_selected"]) is not bool:
            raise ValueError("Fixture selection must be a boolean")

        # All assignment is to an unpublished temporary controller. An exception
        # below leaves the caller's existing desk and replay state untouched.
        desk = DeskController.__new__(DeskController)
        desk._lock = RLock()
        desk.market, desk.portfolio, desk.settings = market, portfolio, settings
        desk.mode, desk.generation = mode, generation
        desk.market_vol, desk.draft_vol, desk.active_vol = market_vol, draft_vol, active_vol
        desk.history, desk._history_inputs = history, history_inputs
        desk._draft_basis = (basis_market, basis_vol, basis_portfolio, basis_settings)
        desk.preview_bundle = None
        desk._preview_basis = None
        desk.error = state["error"]
        saved = state["active_result"]
        if saved.get("status") == "failed":
            try:
                desk._evaluate(market, market_vol if mode == "follow_market" else active_vol)
            except Exception as exc:
                desk.bundle = desk._failed_bundle(str(exc))
            else:
                raise ValueError("Saved failed calculation no longer reproduces")
        else:
            if mode == "follow_market" and active_vol != market_vol:
                raise ValueError("Follow-market session contains a different Active volatility")
            desk.bundle = desk._evaluate(market, active_vol)
        _same_result(saved, desk.bundle, "Active result")
        saved_preview = state["preview_result"]
        if saved_preview is not None:
            if saved.get("status") == "failed":
                raise ValueError("Failed session cannot contain a usable preview")
            p_inputs = state["preview_inputs"]
            p_market, p_portfolio, p_settings = _read_context(p_inputs)
            p_before, p_after = (VolVersion.from_dict(p_inputs[key]) for key in ("before_vol", "after_vol"))
            if p_market.snapshot_id != basis_market.snapshot_id or p_portfolio != basis_portfolio or p_settings != basis_settings or p_after != draft_vol:
                raise ValueError("Preview inputs do not match the frozen Draft inputs")
            if history.get(p_before.version_id) != p_before:
                raise ValueError("Preview before-volatility is absent from history")
            recomputed = {"before":desk._evaluate(p_market,p_before,p_portfolio,p_settings),
                          "after":desk._evaluate(p_market,p_after,p_portfolio,p_settings),
                          "basis_market":p_market.snapshot_id,"basis_portfolio":portfolio_id(p_portfolio),
                          "current_market_changed":p_market.snapshot_id!=market.snapshot_id}
            _same_result(saved_preview, recomputed, "Frozen preview")
            desk.preview_bundle = recomputed
            desk._preview_basis = (p_market,p_before,p_portfolio,p_settings,p_after)
        elif state["preview_inputs"] is not None:
            raise ValueError("Preview inputs provided without a preview result")
        fixture = provenance["fixture_selected"] or any(word in f"{s.mode} {s.source}".lower() for s in snapshots for word in ("engineering", "fixture", "synthetic"))
        return RestoredSession(desk, snapshots, index, tuple(failures), source_hash, tuple(warnings), fixture, content_hash)
    except (KeyError, TypeError, AttributeError, IndexError, OverflowError, UnicodeError) as exc:
        raise ValueError(f"Invalid session: {exc}") from exc
