"""Bounded strike-slice interpolation and explicit volatility conventions."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import replace
import math
import numpy as np
from .models import Contract, VolNode, VolVersion, MarketSnapshot, NORMAL_UNIT, LOGNORMAL_UNIT, utc

def display_vol(node: VolNode) -> float:
    return node.value * (100.0 if node.model == "lognormal" else 1.0)

def from_display(value: float, model: str) -> float:
    return value / 100.0 if model == "lognormal" else value

def vol_for_contract(contract: Contract, version: VolVersion) -> float:
    nodes = sorted((n for n in version.nodes if n.slice_key == contract.slice_key), key=lambda n:n.strike)
    if not nodes or contract.strike < nodes[0].strike or contract.strike > nodes[-1].strike:
        raise ValueError(f"No volatility coverage for {contract.contract_id}; extrapolation is disabled")
    if len({n.strike for n in nodes}) != len(nodes):
        raise ValueError("Duplicate volatility strike in slice")
    return float(np.interp(contract.strike, [n.strike for n in nodes], [n.value for n in nodes]))

def validate_version(version: VolVersion, market: MarketSnapshot) -> list[str]:
    """Finite-grid diagnostics; not a proof of a globally arbitrage-free surface."""
    from .pricing import price, forward_for_contract, time_to_expiry
    errors = []
    grouped = defaultdict(list)
    if not version.nodes:
        return ["No accepted volatility nodes"]
    for n in version.nodes:
        if n.model not in ("normal", "lognormal"):
            errors.append("Unsupported volatility model")
            continue
        expected = NORMAL_UNIT if n.model == "normal" else LOGNORMAL_UNIT
        if n.product not in (("7A", "B7A") if n.model == "normal" else ("LC", "LCE")):
            errors.append("Product/model convention mismatch")
        if n.unit != expected:
            errors.append(f"Unit mismatch for {n.node_id}: expected {expected}")
        if not math.isfinite(n.value) or n.value < 0 or not math.isfinite(n.strike):
            errors.append(f"Invalid volatility or strike at {n.node_id}")
        if n.model == "lognormal" and n.strike <= 0:
            errors.append("Black-76 requires positive strike")
        if not n.source or not n.product or not n.underlyings:
            errors.append("Missing volatility identity or provenance")
        if utc(n.as_of) > utc(market.as_of):
            errors.append("Future volatility node relative to valuation timestamp")
        if utc(n.expiry) <= utc(market.as_of):
            errors.append("Expired volatility slice")
        grouped[n.slice_key].append(n)
    if errors:
        return errors
    for key, nodes in grouped.items():
        nodes.sort(key=lambda n:n.strike)
        if len(set(n.strike for n in nodes)) != len(nodes):
            errors.append("Duplicate strike in volatility slice")
            continue
        product, underlyings, expiry, model = key
        proto = Contract("diagnostic",product,"cso" if model=="normal" else "vanilla",underlyings,expiry,nodes[0].strike,"call")
        try:
            f = forward_for_contract(proto,market)
            t = time_to_expiry(proto,market)
            disc = math.exp(-market.rate*t)
            grid = sorted(set([n.strike for n in nodes] + [a.strike+(b.strike-a.strike)*j/8 for a,b in zip(nodes,nodes[1:]) for j in range(1,8)]))
            calls = []
            for k in grid:
                c = replace(proto,strike=k)
                v = vol_for_contract(c,version)
                call = price(c.kind,f,k,t,v,"call",market.rate)
                put = price(c.kind,f,k,t,v,"put",market.rate)
                tolerance = 1e-8*max(1,abs(f),abs(k))
                if not math.isfinite(call) or call < max(f-k,0)*disc-tolerance or put < max(k-f,0)*disc-tolerance:
                    errors.append(f"Price bound violated in {product} slice")
                if model=="lognormal" and (call>disc*f+tolerance or put>disc*k+tolerance):
                    errors.append("Black-76 price upper bound violated")
                if abs(call-put-disc*(f-k)) > tolerance:
                    errors.append("Put-call parity diagnostic failed")
                calls.append(call)
            if len(grid)>1:
                slopes = np.diff(calls)/np.diff(grid)
                if np.any(slopes>1e-8) or np.any(slopes < -disc-1e-8):
                    errors.append(f"Strike price monotonicity/bound failed in {product} slice")
                if len(slopes)>1 and np.any(np.diff(slopes)<-1e-7):
                    errors.append(f"Strike price convexity failed in {product} slice")
        except (ValueError, KeyError, OverflowError) as exc:
            errors.append(str(exc))
    return list(dict.fromkeys(errors))

def calibrate_market(snapshot: MarketSnapshot) -> VolVersion:
    from .pricing import implied_vol, forward_for_contract, time_to_expiry
    selected = {}
    for c in snapshot.contracts:
        if c.kind == "future":
            continue
        q = snapshot.quote_map.get(c.contract_id)
        if not q or q.mid is None or q.mid < 0 or q.flags:
            continue
        try:
            f = forward_for_contract(c,snapshot)
            t = time_to_expiry(c,snapshot)
            if t <= 0:
                continue
            sigma = implied_vol(c.kind,f,c.strike,t,q.mid,c.right,snapshot.rate)
        except (ValueError, KeyError, OverflowError):
            continue
        n = VolNode(c.product,c.underlyings,c.expiry,c.strike,c.model,float(sigma),NORMAL_UNIT if c.kind=="cso" else LOGNORMAL_UNIT,q.as_of,q.source or snapshot.source,"calibrated",c.contract_id)
        key = (n.slice_key,n.strike)
        # Prefer OTM quotes if both rights exist. This is explicit and deterministic.
        rank = (0 if (c.right=="call" and c.strike>=f) or (c.right=="put" and c.strike<f) else 1,c.contract_id)
        if key not in selected or rank < selected[key][0]:
            selected[key] = (rank,n)
    nodes = tuple(v[1] for _,v in sorted(selected.items()))
    return VolVersion(nodes,"Market calibration","calibrated",None,snapshot.as_of)

def shifted(version: VolVersion, slice_key, amount_display_units: float, as_of: str) -> VolVersion:
    if not math.isfinite(amount_display_units):
        raise ValueError("Non-finite shift")
    nodes = tuple(replace(n,value=n.value+from_display(amount_display_units,n.model),origin="manual",source="User adjustment; base: "+n.source,as_of=as_of) if n.slice_key==slice_key else n for n in version.nodes)
    if nodes == version.nodes:
        raise ValueError("No matching slice")
    return VolVersion(nodes,"Manual draft","manual",version.version_id,as_of)
