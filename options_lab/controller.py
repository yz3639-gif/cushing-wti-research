"""Serial, transactional desk state. A bundle is committed as a single object."""
from __future__ import annotations
from dataclasses import replace
from threading import RLock
from .models import VolVersion, Position, Settings, utc, portfolio_id, ENGINE_VERSION, stable_id
from .volatility import calibrate_market, validate_version, shifted

class DeskController:
    def __init__(self, snapshot, portfolio:tuple[Position,...], settings:Settings):
        self._lock = RLock()
        self.market = snapshot
        self.portfolio = tuple(portfolio)
        self.settings = settings
        self.mode = "follow_market"
        self.generation = 0
        self.error = None
        self.market_vol = calibrate_market(snapshot)
        self.active_vol = self.market_vol
        errors = validate_version(self.active_vol,snapshot)
        if errors:
            raise ValueError("; ".join(errors))
        self.bundle = self._evaluate(snapshot,self.active_vol,self.portfolio,settings)
        self.history = {self.active_vol.version_id:self.active_vol}
        self._history_inputs = {self.active_vol.version_id:(snapshot,self.portfolio,settings)}
        self.reset_draft()

    def _evaluate(self, market, vol, portfolio=None, settings=None):
        from .engine import evaluate
        errors = validate_version(vol,market)
        if errors:
            raise ValueError("; ".join(errors))
        result = evaluate(market,vol,self.portfolio if portfolio is None else portfolio,self.settings if settings is None else settings)
        if result.get("status") in ("error","failed","invalid","incomplete"):
            raise ValueError("Evaluation failed: "+str(result.get("errors", result.get("warnings",[]))))
        return result

    def reset_draft(self):
        with self._lock:
            self.draft_vol = self.active_vol
            self._draft_basis = (self.market,self.active_vol,self.portfolio,self.settings)
            self.preview_bundle = None
            self._preview_basis = None
            self.error = None

    def set_draft(self, nodes):
        with self._lock:
            nodes = tuple(nodes)
            if any(utc(n.as_of) > utc(self._draft_basis[0].as_of) for n in nodes):
                self.error = "Future imported volatility node relative to frozen preview market"
                raise ValueError(self.error)
            edited = tuple(replace(n,origin="manual",as_of=self._draft_basis[0].as_of,source=n.source if n.source.startswith("User adjustment") else "User adjustment; base: "+n.source) for n in nodes)
            self.draft_vol = VolVersion(edited,"Manual draft","manual",self.active_vol.version_id,self._draft_basis[0].as_of)
            self.preview_bundle = None
            self._preview_basis = None

    def shift_slice(self, slice_key, amount_display_units):
        with self._lock:
            self.draft_vol = shifted(self.draft_vol,slice_key,amount_display_units,self._draft_basis[0].as_of)
            self.preview_bundle = None
            self._preview_basis = None

    def preview(self):
        with self._lock:
            market, before, positions, settings = self._draft_basis
            try:
                comparison = {"before":self._evaluate(market,before,positions,settings),"after":self._evaluate(market,self.draft_vol,positions,settings),"basis_market":market.snapshot_id,"basis_portfolio":portfolio_id(positions),"current_market_changed":market.snapshot_id!=self.market.snapshot_id}
                self.preview_bundle = comparison
                self._preview_basis = (market,before,positions,settings,self.draft_vol)
                self.error = None
                return comparison
            except Exception as exc:
                self.error = str(exc)
                self.preview_bundle = None
                self._preview_basis = None
                raise

    def apply(self, expected_generation=None):
        with self._lock:
            if expected_generation is not None and expected_generation != self.generation:
                raise ValueError("State changed since preview; preview again")
            try:
                # Comparison always fixes the same current market and positions.
                comparison_before_vol = self.active_vol
                before = self._evaluate(self.market,self.active_vol)
                result = self._evaluate(self.market,self.draft_vol)
            except Exception as exc:
                self.error = str(exc)
                raise
            self.active_vol = self.draft_vol
            self.mode = "manual"
            self.bundle = result
            self.history[self.active_vol.version_id] = self.active_vol
            self._history_inputs[self.active_vol.version_id] = (self.market,self.portfolio,self.settings)
            self.generation += 1
            self.reset_draft()
            self.preview_bundle = {"before":before,"after":result,"basis_market":self.market.snapshot_id,"basis_portfolio":portfolio_id(self.portfolio),"current_market_changed":False}
            self._preview_basis = (self.market,comparison_before_vol,self.portfolio,self.settings,self.active_vol)
            return result

    def restore(self, version_id):
        with self._lock:
            candidate = self.history[version_id]
            result = self._evaluate(self.market,candidate)
            self.active_vol = candidate
            self.bundle = result
            self.mode = "manual"
            self.generation += 1
            self.reset_draft()
            return result

    def follow_market(self):
        with self._lock:
            result = self._evaluate(self.market,self.market_vol)
            self.active_vol = self.market_vol
            self.bundle = result
            self.mode = "follow_market"
            self.history[self.active_vol.version_id] = self.active_vol
            self._history_inputs[self.active_vol.version_id] = (self.market,self.portfolio,self.settings)
            self.generation += 1
            self.reset_draft()
            return result

    def refresh(self, snapshot):
        with self._lock:
            if utc(snapshot.as_of)<utc(self.market.as_of) or (utc(snapshot.as_of)==utc(self.market.as_of) and snapshot.sequence<=self.market.sequence):
                raise ValueError("Out-of-order or duplicate market update rejected")
            market_vol = VolVersion((),"Calibration unavailable","calibrated",None,snapshot.as_of)
            try:
                market_vol = calibrate_market(snapshot)
                candidate = market_vol if self.mode=="follow_market" else self.active_vol
                result = self._evaluate(snapshot,candidate)
            except Exception as exc:
                # New market + failed computation must never expose an old hedge as current.
                self.market = snapshot
                self.market_vol = market_vol
                self.error = str(exc)
                self.generation += 1
                self.preview_bundle = None
                self._preview_basis = None
                self.bundle = self._failed_bundle(str(exc))
                raise
            self.market = snapshot
            self.market_vol = market_vol
            self.active_vol = candidate
            self.bundle = result
            self.history[candidate.version_id] = candidate
            self._history_inputs[candidate.version_id] = (self.market,self.portfolio,self.settings)
            self.generation += 1
            self.error = None
            self.preview_bundle = None
            self._preview_basis = None
            # Preserve an in-progress draft and its frozen comparison basis.
            if self.draft_vol == self._draft_basis[1]:
                self.reset_draft()
            return result

    def _failed_bundle(self, error):
        """No advice survives a failed calculation for the current market."""
        snapshot = self.market
        ids = dict(snapshot_id=snapshot.snapshot_id,vol_version_id=self.active_vol.version_id,portfolio_id=portfolio_id(self.portfolio),settings_id=self.settings.settings_id,engine_version=ENGINE_VERSION)
        return {**ids,"bundle_id":stable_id(ids,"failed-"),"as_of":snapshot.as_of,"mode":snapshot.mode,"status":"failed","prices":[],"quotes":[],"hedges":{},"risk":{},"errors":[error],"warnings":["Current calculation unavailable; old hedge recommendations removed"]}

    def set_inputs(self, portfolio, settings):
        with self._lock:
            result = self._evaluate(self.market,self.active_vol,tuple(portfolio),settings)
            self.portfolio = tuple(portfolio)
            self.settings = settings
            self.bundle = result
            self.generation += 1
            self.reset_draft()
            return result
