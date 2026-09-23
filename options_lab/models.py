"""Immutable, content-addressed inputs shared by every desk output."""
from __future__ import annotations
from dataclasses import dataclass, asdict, fields
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Any

NORMAL_UNIT = "usd_per_bbl_sqrt_year"
LOGNORMAL_UNIT = "decimal_annual"
ENGINE_VERSION = "0.2.0"

def utc(value: str | datetime) -> datetime:
    dt = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone-aware timestamp required")
    return dt.astimezone(timezone.utc)

def stable_id(value: Any, prefix: str = "") -> str:
    if hasattr(value, "__dataclass_fields__"):
        value = asdict(value)
    return prefix + sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:20]

class Serializable:
    def to_dict(self):
        return asdict(self)

@dataclass(frozen=True)
class Contract(Serializable):
    contract_id: str
    product: str
    kind: str
    underlyings: tuple[str, ...]
    expiry: str
    strike: float | None = None
    right: str | None = None
    multiplier: float = 1000.0
    tick_size: float = 0.01
    currency: str = "USD"
    exercise: str = "european"
    source: str = ""

    def __post_init__(self):
        object.__setattr__(self, "underlyings", tuple(self.underlyings))
        utc(self.expiry)
        if self.kind not in ("future", "vanilla", "cso"):
            raise ValueError("Unsupported contract kind")
        if not self.contract_id or not self.product or self.multiplier <= 0 or self.tick_size <= 0:
            raise ValueError("Invalid contract identity or units")
        if self.kind != "future":
            if self.right not in ("call", "put") or self.strike is None or not math.isfinite(self.strike):
                raise ValueError("Option requires finite strike and call/put right")
            if self.exercise != "european":
                raise ValueError("Only European options supported")
            if len(self.underlyings) != (2 if self.kind == "cso" else 1):
                raise ValueError("Invalid exact underlying chain")

    @property
    def model(self):
        return "normal" if self.kind == "cso" else "lognormal"

    @property
    def slice_key(self):
        return (self.product, self.underlyings, self.expiry, self.model)

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

@dataclass(frozen=True)
class Quote(Serializable):
    contract_id: str
    as_of: str
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    mark: float | None = None
    kind: str = "bbo"
    source: str = ""
    flags: tuple[str, ...] = ()

    def __post_init__(self):
        object.__setattr__(self, "flags", tuple(self.flags))
        utc(self.as_of)
        for v in (self.bid, self.ask, self.mark, self.bid_size, self.ask_size):
            if v is not None and not math.isfinite(v):
                raise ValueError("Quote values must be finite")
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("Crossed source quote")
        if any(v is not None and v < 0 for v in (self.bid_size, self.ask_size)):
            raise ValueError("Negative displayed size")

    @property
    def mid(self):
        if self.bid is not None and self.ask is not None:
            return (self.bid + self.ask) / 2
        return self.mark

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

@dataclass(frozen=True)
class MarketSnapshot(Serializable):
    contracts: tuple[Contract, ...]
    quotes: tuple[Quote, ...]
    as_of: str
    mode: str = "observed_eod"
    source: str = ""
    sequence: int = 0
    rate: float = 0.0
    feed_alive: bool = True
    received_at: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "contracts", tuple(self.contracts))
        object.__setattr__(self, "quotes", tuple(self.quotes))
        if not isinstance(self.feed_alive, bool):
            raise ValueError("feed_alive must be a boolean")
        event = utc(self.as_of)
        if self.received_at:
            utc(self.received_at)
        if len(self.contract_map) != len(self.contracts) or len(self.quote_map) != len(self.quotes):
            raise ValueError("Duplicate contract or quote IDs")
        for q in self.quotes:
            if q.contract_id not in self.contract_map:
                raise ValueError("Unknown quote contract")
            if utc(q.as_of) > event:
                raise ValueError("Future quote relative to snapshot: look-ahead rejected")
        if not math.isfinite(self.rate):
            raise ValueError("Invalid rate")

    @property
    def contract_map(self):
        return {x.contract_id: x for x in self.contracts}

    @property
    def quote_map(self):
        return {x.contract_id: x for x in self.quotes}

    @property
    def snapshot_id(self):
        return stable_id(self, "mkt-")

    @classmethod
    def from_dict(cls, data):
        d = dict(data)
        d["contracts"] = tuple(Contract.from_dict(x) for x in d["contracts"])
        d["quotes"] = tuple(Quote.from_dict(x) for x in d["quotes"])
        return cls(**d)

@dataclass(frozen=True)
class VolNode(Serializable):
    product: str
    underlyings: tuple[str, ...]
    expiry: str
    strike: float
    model: str
    value: float
    unit: str
    as_of: str
    source: str
    origin: str = "calibrated"
    contract_id: str = ""

    def __post_init__(self):
        object.__setattr__(self, "underlyings", tuple(self.underlyings))
        utc(self.as_of)
        utc(self.expiry)

    @property
    def slice_key(self):
        return (self.product, self.underlyings, self.expiry, self.model)

    @property
    def node_id(self):
        return stable_id((self.slice_key, self.strike), "node-")

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

@dataclass(frozen=True)
class VolVersion(Serializable):
    nodes: tuple[VolNode, ...]
    label: str = "Market calibration"
    origin: str = "calibrated"
    parent_id: str | None = None
    created_at: str = "2026-01-01T00:00:00+00:00"

    def __post_init__(self):
        object.__setattr__(self, "nodes", tuple(self.nodes))
        utc(self.created_at)

    @property
    def version_id(self):
        return stable_id(self, "vol-")

    @classmethod
    def from_dict(cls, data):
        d = dict(data)
        d["nodes"] = tuple(VolNode.from_dict(x) for x in d["nodes"])
        return cls(**d)

@dataclass(frozen=True)
class Position(Serializable):
    contract_id: str
    quantity: int

    def __post_init__(self):
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise ValueError("Positions require integer lots")

@dataclass(frozen=True)
class Settings(Serializable):
    target_id: str = ""
    allow_cso_hedge: bool = False
    future_bound: int = 20
    option_bound: int = 10
    position_limit: int = 40
    gross_lot_limit: int = 40
    risk_limit_dollars: float = 25000.0
    fee_per_contract: float = 0.0
    fee_confirmed: bool = False
    max_quote_age_seconds: float = 300.0
    max_underlying_age_seconds: float = 60.0
    max_transport_delay_seconds: float = 15.0
    clip_size: int = 1
    hedge_time_limit_seconds: float = 0.75
    hedge_node_limit: int = 8
    risk_aversion: float = 0.1
    risk_scale_dollars: float = 25000.0
    horizon_days: float = 1.0
    scenario_count: int = 100

    def __post_init__(self):
        for name in ("future_bound", "option_bound", "position_limit", "gross_lot_limit", "clip_size", "scenario_count", "hedge_node_limit"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError("Lot, scenario and solver-node limits must be integers")
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, (float, int)) and not math.isfinite(value):
                raise ValueError("Settings must be finite")
        if any(getattr(self, k) < 0 for k in ("future_bound", "option_bound", "position_limit", "gross_lot_limit", "risk_limit_dollars", "fee_per_contract", "max_quote_age_seconds", "max_underlying_age_seconds", "max_transport_delay_seconds", "risk_aversion")):
            raise ValueError("Negative settings limit")
        if not 7 <= self.scenario_count <= 1000 or self.hedge_node_limit < 1 or self.hedge_time_limit_seconds <= 0 or self.clip_size < 1 or self.risk_scale_dollars <= 0 or self.horizon_days < 0:
            raise ValueError("Invalid scenario, clip, solver or risk settings")

    @property
    def settings_id(self):
        return stable_id(self, "cfg-")

    @classmethod
    def from_dict(cls, data):
        return cls(**data)

ResultBundle = dict[str, Any]

def portfolio_id(portfolio):
    return stable_id([asdict(p) for p in portfolio], "pos-")
