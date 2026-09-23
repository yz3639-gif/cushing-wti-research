from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
import math
import pytest
from options_lab.models import *
from options_lab.pricing import price
from options_lab.volatility import *
from options_lab.controller import DeskController

def sample():
    at="2025-06-02T18:00:00+00:00"
    expiry="2025-07-15T18:00:00+00:00"
    cs=[Contract("CLN5","CL","future",(),"2025-07-20T18:00:00+00:00",source="Engineering fixture"),Contract("CLQ5","CL","future",(),"2025-08-20T18:00:00+00:00",source="Engineering fixture")]
    qs=[Quote("CLN5",at,74.99,75.01,100,100,source="Engineering fixture"),Quote("CLQ5",at,73.99,74.01,100,100,source="Engineering fixture")]
    t=(utc(expiry)-utc(at)).total_seconds()/(365*86400)
    for kind,product,strikes,under,fwd,vol in [("cso","B7A",[-1.,0.,1.],("CLN5","CLQ5"),1.,4.),("vanilla","LCE",[70.,75.,80.],("CLN5",),75.,.30)]:
        for k in strikes:
            c=Contract(f"{product}N5_C{k}",product,kind,under,expiry,k,"call",source="Engineering fixture")
            p=price(kind,fwd,k,t,vol,"call")
            cs.append(c); qs.append(Quote(c.contract_id,at,max(0,p-.001),p+.001,100,100,source="Engineering fixture"))
    return MarketSnapshot(tuple(cs),tuple(qs),at,"engineering_fixture","Engineering fixture"), (Position("B7AN5_C1.0",-10),), Settings(target_id="B7AN5_C1.0",scenario_count=7)

def desk():
    return DeskController(*sample())

def test_draft_isolation_apply_versions_restore():
    d=desk(); original=d.bundle; originalvol=d.active_vol
    d.shift_slice(d.active_vol.nodes[0].slice_key, .3)
    assert d.active_vol==originalvol and d.bundle==original
    comparison=d.preview()
    assert comparison["before"]["snapshot_id"]==comparison["after"]["snapshot_id"]
    assert comparison["before"]["portfolio_id"]==comparison["after"]["portfolio_id"]
    d.apply()
    assert d.bundle["vol_version_id"]==d.active_vol.version_id
    assert d.mode=="manual" and d.active_vol!=originalvol
    restored=d.restore(originalvol.version_id)
    assert restored["bundle_id"]==original["bundle_id"]
    assert restored["risk"]==original["risk"]

def test_manual_market_refresh_and_follow():
    d=desk(); d.shift_slice(d.active_vol.nodes[0].slice_key,.2); d.apply(); active=d.active_vol
    d.refresh(replace(d.market,sequence=1))
    assert d.active_vol==active and d.bundle["snapshot_id"]==d.market.snapshot_id
    d.follow_market(); assert d.active_vol==d.market_vol and d.mode=="follow_market"

def test_units_and_zero_normal_negative_strikes():
    s,_,_=sample(); v=calibrate_market(s)
    n=next(n for n in v.nodes if n.model=="lognormal")
    shiftedv=shifted(v,n.slice_key,2.0,s.as_of)
    assert vol_for_contract(next(c for c in s.contracts if c.contract_id==n.contract_id),shiftedv)==pytest.approx(n.value+.02)
    assert display_vol(n)==pytest.approx(n.value*100)
    assert price("cso",-2,-3,.5,0,"call")==1
    assert price("cso",-2,-3,.5,0,"put")==0
    assert price("cso",-2,-1,.5,0,"put")==1

def test_no_extrapolation_cross_month_or_expiry():
    s,_,_=sample(); v=calibrate_market(s); c=next(c for c in s.contracts if c.kind=="cso")
    for bad in [replace(c,strike=999),replace(c,expiry="2025-07-16T18:00:00+00:00"),replace(c,underlyings=("CLQ5","CLN5"))]:
        with pytest.raises(ValueError,match="coverage"): vol_for_contract(bad,v)

def test_failed_apply_preserves_last_valid_bundle():
    d=desk(); old=d.bundle; vol=d.active_vol
    d.set_draft((replace(d.draft_vol.nodes[0],value=-1),)+d.draft_vol.nodes[1:])
    with pytest.raises(ValueError): d.apply()
    assert d.active_vol==vol and d.bundle==old

def test_future_import_cannot_be_restamped_into_a_valid_draft():
    d=desk(); original=d.draft_vol
    incoming=(replace(original.nodes[0],as_of="2030-01-01T00:00:00Z"),)+original.nodes[1:]
    with pytest.raises(ValueError,match="Future imported"):
        d.set_draft(incoming)
    assert d.draft_vol==original

def test_invalid_units_convexity_and_future_nodes():
    s,_,_=sample(); v=calibrate_market(s)
    assert validate_version(replace(v,nodes=(replace(v.nodes[0],unit=LOGNORMAL_UNIT),)+v.nodes[1:]),s)
    assert validate_version(replace(v,nodes=(replace(v.nodes[0],as_of="2028-01-01T00:00:00Z"),)+v.nodes[1:]),s)
    # An extreme isolated volatility spike yields an invalid strike-price shape.
    normal=[n for n in v.nodes if n.model=="normal"]
    bad=replace(v,nodes=tuple(replace(n,value=100) if n==normal[1] else n for n in v.nodes))
    assert any("convexity" in e or "monotonicity" in e for e in validate_version(bad,s))

def test_failed_refresh_clears_old_proposals():
    d=desk(); original=d.active_vol
    bad=replace(d.market,quotes=tuple(q for q in d.market.quotes if d.market.contract_map[q.contract_id].kind=="future"),sequence=1)
    with pytest.raises(ValueError):d.refresh(bad)
    assert d.active_vol==original and not d.bundle["hedges"] and not d.bundle["quotes"]

def test_concurrent_updates_never_half_commit():
    d=desk(); basis=d.market
    def update(i):
        try:return d.refresh(replace(basis,sequence=i))
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=4) as executor:
        results=list(executor.map(update,range(1,17)))
    for r in filter(None,results):
        assert r["vol_version_id"] in d.history
        assert r["settings_id"]==d.settings.settings_id
        assert r["portfolio_id"]==portfolio_id(d.portfolio)
    assert d.market.sequence==16
    assert d.bundle["snapshot_id"]==d.market.snapshot_id
    with pytest.raises(ValueError,match="changed"):d.apply(expected_generation=-1)
