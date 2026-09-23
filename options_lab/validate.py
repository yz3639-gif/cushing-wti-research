"""Reproduce numerical tests, warm pipeline timings and preservation evidence."""
from __future__ import annotations
import argparse
from dataclasses import replace
import hashlib
import json
import math
from datetime import timedelta
from pathlib import Path
import platform
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import numpy as np

ROOT=Path(__file__).resolve().parents[1]

def source_hashes():
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for folder in ("options_lab","tests_options") for p in sorted((ROOT/folder).rglob("*.py"))}

def guard_original():
    path=ROOT/"options_lab_runs/original_guard.json"
    if not path.exists():
        path=ROOT/"options_lab/examples/research_baseline.json"
    if not path.exists():
        return {"status":"pending","reason":"No pre-change hash baseline present"}
    baseline=json.loads(path.read_text())
    changed=[name for name,digest in baseline.items() if not (ROOT/name).is_file() or hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest]
    return {"status":"passed" if not changed else "failed","checked_files":len(baseline),"changed_files":changed,"baseline_sha256":hashlib.sha256(path.read_bytes()).hexdigest(),"baseline_source":str(path.relative_to(ROOT))}

def acceptance_errors(report):
    """Engineering acceptance only; missing real-data/live evidence stays pending."""
    errors=[]
    if report.get('tests',{}).get('status')!='passed': errors.append('Automated tests not passed')
    p=report.get('performance',{}); inv=report.get('invariants',{})
    for key, minimum in (('updates',1000),('contracts',20),('scenarios',100),('distinct_input_states',1000)):
        value=p.get(key)
        if type(value) is not int or value<minimum: errors.append(f'{key} must be an integer >= {minimum}')
    latency=p.get('p95_seconds')
    if type(latency) not in (int,float) or not math.isfinite(latency) or not 0<=latency<=2:
        errors.append('Warm core p95 must be finite and <= 2 seconds')
    if type(p.get('failure_count')) is not int or p['failure_count']!=0: errors.append('Pipeline update failures')
    for key in ('negative_or_crossed_quotes','mixed_versions','constraint_violations','unavailable_bundles'):
        if type(inv.get(key)) is not int or inv[key]!=0: errors.append(key)
    reconciliation=inv.get('max_leg_portfolio_reconciliation_usd')
    if type(reconciliation) not in (int,float) or not math.isfinite(reconciliation) or not 0<=reconciliation<=.01:
        errors.append('Leg/portfolio reconciliation exceeds $0.01 or is unavailable')
    if report.get('original_research',{}).get('status')!='passed': errors.append('Original research preservation not passed')
    return errors


def benchmark_snapshot(initial, sequence):
    """Changing synthetic states; generation is outside the measured input pipeline."""
    from .models import utc
    from .pricing import instrument_value
    from .volatility import calibrate_market, vol_for_contract
    version=calibrate_market(initial)
    at=(utc(initial.as_of)+timedelta(seconds=sequence)).isoformat()
    futures=[c for c in initial.contracts if c.kind=='future']
    shifts={c.contract_id: .6*math.sin(sequence*.037)+(j-(len(futures)-1)/2)*.08*math.cos(sequence*.019)
            for j,c in enumerate(futures)}
    quotes=[]
    for q in initial.quotes:
        shift=shifts.get(q.contract_id,0)
        quotes.append(replace(q,as_of=at,bid=None if q.bid is None else q.bid+shift,
                              ask=None if q.ask is None else q.ask+shift,
                              mark=None if q.mark is None else q.mark+shift))
    moving=replace(initial,quotes=tuple(quotes),as_of=at,received_at=None,sequence=sequence,mode='engineering_fixture')
    repriced=[]
    for q in moving.quotes:
        contract=moving.contract_map[q.contract_id]
        if contract.kind=='future': repriced.append(q); continue
        factor=1+.08*math.sin(sequence*(.023 if contract.kind=='cso' else .017))
        value=instrument_value(contract,moving,vol_for_contract(contract,version)*factor)
        half=min((q.ask-q.bid)/2,value*.5) if q.bid is not None and q.ask is not None else 0
        repriced.append(replace(q,bid=None if q.bid is None else value-half,
                               ask=None if q.ask is None else value+half,
                               mark=None if q.mark is None else value))
    return replace(moving,quotes=tuple(repriced))


def bundle_invariants(bundle, market, active_vol, portfolio, settings):
    from .models import portfolio_id
    mixed=int(any(bundle.get(k)!=v for k,v in {
        'snapshot_id':market.snapshot_id,'vol_version_id':active_vol.version_id,
        'portfolio_id':portfolio_id(portfolio),'settings_id':settings.settings_id}.items()))
    quote_errors=0; violations=0
    target_ids=[settings.target_id] if settings.target_id else [c.contract_id for c in market.contracts if c.kind=='cso'][:1]
    quote_errors+=int([q.get('contract_id') for q in bundle.get('quotes',[])]!=target_ids)
    for q in bundle.get('quotes',[]):
        bid,ask=q.get('bid'),q.get('ask')
        prices_ok=all(type(v) in (int,float) and math.isfinite(v) and v>=0 for v in (bid,ask))
        quote_errors+=int(not prices_ok or (prices_ok and bid>=ask) or q.get('status')!='indicative')
        current=sum(p.quantity for p in portfolio if p.contract_id==q['contract_id'])
        for sign,key in ((1,'bid_size'),(-1,'ask_size')):
            size=q.get(key,0)
            violations+=int(isinstance(size,bool) or not isinstance(size,int) or size<0
                            or (size>0 and abs(current+sign*size)>settings.position_limit))
    for name,h in bundle.get('hedges',{}).items():
        if h['status'] not in ('optimal','feasible_limit','fallback_zero','fallback_futures'):
            violations+=1; continue
        final={p.contract_id:sum(x.quantity for x in portfolio if x.contract_id==p.contract_id) for p in portfolio}
        total=0; expected_cost=0.; seen=set()
        for t in h['trades']:
            identifier=t.get('contract_id'); quantity=t.get('quantity')
            if identifier not in market.contract_map or type(quantity) is not int or identifier in seen:
                violations+=1; continue
            seen.add(identifier); contract=market.contract_map[identifier]
            bound=settings.future_bound if contract.kind=='future' else settings.option_bound
            violations+=int(abs(quantity)>bound or (contract.kind=='cso' and not settings.allow_cso_hedge)
                            or (name=='delta' and contract.kind!='future'))
            quote=market.quote_map.get(identifier)
            if quote is None or quote.flags: violations+=1
            if quote and quote.kind=='bbo' and quote.bid is not None and quote.ask is not None:
                half=(quote.ask-quote.bid)/2
                size=quote.ask_size if quantity>0 else quote.bid_size
                violations+=int(size is not None and abs(quantity)>size)
            else: half=contract.tick_size
            expected_cost+=abs(quantity)*(half*contract.multiplier+settings.fee_per_contract)
            final[identifier]=final.get(identifier,0)+quantity; total+=abs(quantity)
        violations+=int(total>settings.gross_lot_limit or any(abs(q)>settings.position_limit for q in final.values()))
        report=h.get('position_constraints',{})
        violations+=int(report.get('scope')!='full_portfolio' or report.get('compliant') is not True
                        or report.get('final_breaches')!={})
        gross=np.asarray(h.get('gross_pnl',[]),dtype=float); net=np.asarray(h.get('net_pnl',[]),dtype=float)
        cost,objective=h.get('cost'),h.get('objective')
        valid=(gross.shape==net.shape==(settings.scenario_count,) and np.isfinite(gross).all()
               and np.isfinite(net).all() and type(cost) in (int,float) and math.isfinite(cost) and cost>=0
               and type(objective) in (int,float) and math.isfinite(objective) and objective>=0)
        if not valid: violations+=1; continue
        expected_objective=float(np.max(np.abs(gross)))+cost
        violations+=int(abs(cost-expected_cost)>.01 or np.max(np.abs(net-(gross-cost)))>.01
                        or abs(objective-expected_objective)>.01 or expected_objective>settings.risk_limit_dollars+1e-5
                        or np.max(np.abs(net))>settings.risk_limit_dollars+1e-5)
        rows=bundle.get('risk',{}).get('scenarios',[])
        if len(rows)!=settings.scenario_count: violations+=1
        elif any(abs(row.get(f'{name}_net',float('inf'))-net[i])>.01
                 or abs(row.get(f'{name}_gross',float('inf'))-gross[i])>.01 for i,row in enumerate(rows)):
            violations+=1
    unavailable=int(bundle.get('status') not in ('ok','analysis_only') or len(bundle.get('hedges',{}))!=2)
    return quote_errors,mixed,violations,unavailable


def run(updates=1000,tests=True,output=None):
    from .adapters import engineering_fixture
    from .models import MarketSnapshot
    from .controller import DeskController
    from .research_validation import evaluate_history
    out=Path(output) if output else ROOT/"options_lab_runs/validation"
    out.mkdir(parents=True,exist_ok=True)
    test_status={"status":"not_run"}
    if tests:
        started=time.perf_counter()
        completed=subprocess.run([sys.executable,"-m","pytest","tests_options","-q",f"--junitxml={out/'tests.xml'}"],cwd=ROOT,capture_output=True,text=True)
        (out/"tests.log").write_text(completed.stdout+completed.stderr)
        print(completed.stdout,flush=True)
        suites=ET.parse(out/"tests.xml").getroot()
        suite=list(suites)[0] if suites.tag=="testsuites" else suites
        test_status={"status":"passed" if completed.returncode==0 else "failed","tests":int(suite.get("tests",0)),"failures":int(suite.get("failures",0))+int(suite.get("errors",0)),"skipped":int(suite.get("skipped",0)),"elapsed_seconds":time.perf_counter()-started}
    data=engineering_fixture(); initial=data.snapshots[0]
    t=time.perf_counter(); desk=DeskController(initial,data.portfolio,data.settings)
    cold=time.perf_counter()-t
    latencies=[]; failures=[]; hedge_states={}; quote_errors=0; mixed_versions=0; max_reconciliation=0
    constraint_violations=0; unavailable_bundles=0; input_states=set()
    # Five warmup updates excluded from reported timing, with distinct market versions.
    for i in range(1,6): desk.refresh(benchmark_snapshot(initial,i))
    for i in range(6,updates+6):
        payload=benchmark_snapshot(initial,i).to_dict()
        input_states.add(hashlib.sha256(json.dumps([(q['contract_id'],q['bid'],q['ask'],q['mark']) for q in payload['quotes']]).encode()).hexdigest())
        started=time.perf_counter()
        try:
            market=MarketSnapshot.from_dict(payload)
            result=desk.refresh(market)
            serialized=json.dumps(result,sort_keys=True,allow_nan=False)
            decoded=json.loads(serialized)
            bad_quotes,mixed,bad_constraints,unavailable=bundle_invariants(decoded,market,desk.active_vol,desk.portfolio,desk.settings)
            quote_errors+=bad_quotes; mixed_versions+=mixed
            constraint_violations+=bad_constraints; unavailable_bundles+=unavailable
            max_reconciliation=max(max_reconciliation,result["risk"].get("reconciliation_error",0))
            for name,h in result["hedges"].items():
                key=name+":"+h["status"]; hedge_states[key]=hedge_states.get(key,0)+1
        except Exception as exc:
            failures.append({"update":i-5,"error":str(exc)})
        latencies.append(time.perf_counter()-started)
        if (i-5)%100==0: print(f"Validated {i-5}/{updates} pipeline updates",flush=True)
    p95=float(np.percentile(latencies,95)) if latencies else None
    report={"data_evidence":"Engineering fixture - not market data","tests":test_status,
      "performance":{"status":"passed" if updates>=1000 and p95<=2 and not failures else "incomplete_or_failed","scope":"snapshot deserialization -> calibration -> strike diagnostics -> atomic controller -> full scenario pricing -> integer optimizers -> complete JSON export; browser network/paint excluded","contracts":len(initial.contracts),"scenarios":len(desk.bundle["risk"].get("scenarios",[])),"updates":updates,"warmups":5,"cold_seconds":cold,"p50_seconds":float(np.percentile(latencies,50)),"p95_seconds":p95,"max_seconds":max(latencies),"failure_count":len(failures),"failures":failures,"solver_statuses":hedge_states},
      "invariants":{"negative_or_crossed_quotes":quote_errors,"mixed_versions":mixed_versions,"constraint_violations":constraint_violations,"unavailable_bundles":unavailable_bundles,"max_leg_portfolio_reconciliation_usd":max_reconciliation},
      "original_research":guard_original(),"historical_study":evaluate_history([]),
      "real_snapshot_acceptance":{"status":"pending","reason":"No authorized CL + European LC/LCE + financial 7A/B7A snapshot acquired"},
      "live_acceptance":{"status":"pending","connected_minutes":0,"real_feed_disconnect_recovery_test":False},
      "environment":{"python":sys.version,"platform":platform.platform(),"processor":platform.processor(),"executable":sys.executable},"source_hashes":source_hashes()}
    report['performance']['distinct_input_states']=len(input_states)
    report['performance']['input_design']='Changing synthetic futures prices, normal/lognormal volatility and timestamps; not observed market traffic'
    errors=acceptance_errors(report)
    report['engineering_acceptance']={'status':'failed' if errors else 'passed','errors':errors}
    report['desk_policy_history']={'status':'pending','module':'options_lab.desk_history','real_sessions':0}
    (out/"report.json").write_text(json.dumps(report,indent=2,allow_nan=False))
    (out/"final_bundle.json").write_text(json.dumps(desk.bundle,indent=2,allow_nan=False))
    (out/"latencies.json").write_text(json.dumps(latencies))
    lines=["# WTI Options Desk - Reproducible Validation", "", "Engineering fixture only. Real-data accuracy, hedge improvement and live operation remain unverified.","",f"- Automated tests: {test_status}",f"- Warm pipeline: {updates} updates, {len(initial.contracts)} contracts, {report['performance']['scenarios']} scenarios; p95 {p95:.4f} s; failures {len(failures)}.",f"- Timing scope: {report['performance']['scope']}",f"- Solver statuses: {hedge_states}",f"- Invariants: {report['invariants']}",f"- Original research: {report['original_research']}","- 120-day / final-40-day study: pending; no empirical hedge advantage established.","- Real feed: pending, 0 connected minutes; no claim of 60-minute acceptance.","","Reproduce from the repository root:","```sh",".venv-options/bin/python -m options_lab.validate --updates 1000","```","","Full environment, code hashes, JUnit results, latency samples and final versioned bundle are alongside this report."]
    (out/"REPORT.md").write_text("\n".join(lines)+"\n")
    with (out/'REPORT.md').open('a') as handle:
        handle.write(f"\nEngineering hard-gate acceptance: {report['engineering_acceptance']}\n")
        handle.write(f"\nDistinct changing input states: {len(input_states)}. {report['performance']['input_design']}.\n")
        handle.write("\nOriginal ridge benchmark and desk-policy historical evaluation are distinct; neither has real-data completion evidence.\n")
    print(json.dumps({"report":str(out/"REPORT.md"),"tests":test_status,"p95_seconds":p95,"failures":len(failures)}),flush=True)
    return report

if __name__=="__main__":
    parser=argparse.ArgumentParser(); parser.add_argument("--updates",type=int,default=1000); parser.add_argument("--skip-tests",action="store_true"); parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    if args.updates<1:parser.error("--updates must be positive")
    r=run(args.updates,not args.skip_tests,args.output)
    sys.exit(1 if acceptance_errors(r) else 0)
