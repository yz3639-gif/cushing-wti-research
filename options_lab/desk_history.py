"""Paired historical episodes using the same evaluate() policy as the desk UI.

Policies and exact contract identities are declared before the final 40 sessions.
Each episode starts with existing CSO inventory and opens/closes its own hedge.
No continuous portfolio, automatic rollover inference or source authenticity claim.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from .adapters import validate_snapshot
from .engine import evaluate
from .models import MarketSnapshot, Position, Settings, portfolio_id, stable_id, utc
from .research_validation import HistoryValidationError, _metric, _paired_bootstrap
from .volatility import calibrate_market, validate_version

FEASIBLE = {'optimal','feasible_limit','fallback_zero','fallback_futures'}


@dataclass(frozen=True)
class DeskDecision:
    snapshot: MarketSnapshot
    portfolio: tuple[Position, ...]
    settings: Settings
    known_at: str
    source_sha256: str


@dataclass(frozen=True)
class DeskEpisode:
    decision: DeskDecision
    exit_snapshot: MarketSnapshot
    exit_source_sha256: str
    policy_id: str
    policy_declared_at: str


def _valid_hash(value):
    return isinstance(value,str) and len(value)==64 and all(c in '0123456789abcdef' for c in value)


def _validate_episode(episode):
    d=episode.decision; snapshot=d.snapshot; end=episode.exit_snapshot
    validate_snapshot(snapshot); validate_snapshot(end)
    if not _valid_hash(d.source_sha256) or not _valid_hash(episode.exit_source_sha256):
        raise HistoryValidationError('Raw source SHA256 values are required')
    if not episode.policy_id.strip(): raise HistoryValidationError('Explicit policy_id required')
    if utc(d.known_at)>utc(snapshot.as_of):
        raise HistoryValidationError('Decision input became known after decision time')
    if snapshot.received_at and utc(snapshot.received_at)>utc(d.known_at):
        raise HistoryValidationError('Decision claims knowledge before snapshot received_at')
    if utc(episode.policy_declared_at)>utc(snapshot.as_of):
        raise HistoryValidationError('Policy must be declared before the decision')
    if not utc(snapshot.as_of)<utc(end.as_of)<=datetime.now(timezone.utc):
        raise HistoryValidationError('Historical exit must follow decision and cannot be in the future')
    target=d.settings.target_id
    if (len(d.portfolio)!=1 or d.portfolio[0].contract_id!=target
            or not d.portfolio[0].quantity or snapshot.contract_map[target].kind!='cso'):
        raise HistoryValidationError('Paired protocol requires one nonzero initial target CSO position')
    if d.settings.allow_cso_hedge:
        raise HistoryValidationError('Paired protocol forbids direct CSO offset')
    if not d.settings.fee_confirmed:
        raise HistoryValidationError('Historical execution-cost fee must be explicitly confirmed')
    if utc(snapshot.contract_map[target].expiry)<=utc(end.as_of):
        raise HistoryValidationError('Episode crossing expiry needs an explicit settlement policy')


def decision_bundle(decision: DeskDecision):
    """No realized market is available to the actual desk policy."""
    vol=calibrate_market(decision.snapshot)
    errors=validate_version(vol,decision.snapshot)
    if errors: raise HistoryValidationError('; '.join(errors))
    bundle=evaluate(decision.snapshot,vol,decision.portfolio,decision.settings)
    if bundle['status'] not in {'ok','analysis_only'}:
        raise HistoryValidationError('Desk decision unavailable: '+bundle['status'])
    if any(bundle['hedges'][k]['status'] not in FEASIBLE for k in ('delta','proxy')):
        raise HistoryValidationError('No jointly feasible futures/proxy comparison')
    return bundle


def _observed_pair(contract, decision, end, settings, *, executable):
    identifier=contract.contract_id
    if utc(contract.expiry)<=utc(end.as_of):
        raise HistoryValidationError('Contract expiry crossed; explicit settlement policy required: '+identifier)
    if end.contract_map.get(identifier)!=contract:
        raise HistoryValidationError('Exact contract identity changed or missing at exit: '+identifier)
    marks=[]; spreads=[]
    for snapshot in (decision,end):
        q=snapshot.quote_map.get(identifier)
        if not snapshot.feed_alive or q is None or q.flags or q.mid is None:
            raise HistoryValidationError('Missing/flagged/disconnected historical mark: '+identifier)
        limit=settings.max_underlying_age_seconds if contract.kind=='future' else settings.max_quote_age_seconds
        if (utc(snapshot.as_of)-utc(q.as_of)).total_seconds()>limit:
            raise HistoryValidationError('Stale historical mark: '+identifier)
        if contract.kind!='future' and any(v is not None and v<0 for v in (q.bid,q.ask,q.mark)):
            raise HistoryValidationError('Negative historical option premium')
        if executable and (q.kind!='bbo' or q.bid is None or q.ask is None):
            raise HistoryValidationError('Observed entry AND exit BBO required for selected hedge: '+identifier)
        marks.append(q.mid)
        spreads.append((q.ask-q.bid)/2 if q.bid is not None and q.ask is not None else 0)
    return marks,spreads


def evaluate_episode(episode: DeskEpisode):
    _validate_episode(episode)
    d=episode.decision; snapshot=d.snapshot; end=episode.exit_snapshot
    bundle=decision_bundle(d)
    target=snapshot.contract_map[d.settings.target_id]
    marks,_=_observed_pair(target,snapshot,end,d.settings,executable=False)
    target_pnl=d.portfolio[0].quantity*target.multiplier*(marks[1]-marks[0])
    rows=[]
    for strategy,key in (('unhedged',None),('futures_only','delta'),('futures_vanilla','proxy')):
        hedge=bundle['hedges'][key] if key else {'trades':[],'status':'not_applicable'}
        gross=target_pnl; cost=0.; legs={target.contract_id:target_pnl}
        for trade in hedge['trades']:
            c=snapshot.contract_map[trade['contract_id']]; quantity=trade['quantity']
            marks,spreads=_observed_pair(c,snapshot,end,d.settings,executable=True)
            leg=quantity*c.multiplier*(marks[1]-marks[0])
            gross+=leg; legs[c.contract_id]=legs.get(c.contract_id,0)+leg
            cost+=abs(quantity)*(sum(spreads)*c.multiplier+2*d.settings.fee_per_contract)
        rows.append({'strategy':strategy,'holdings':{t['contract_id']:t['quantity'] for t in hedge['trades']},
                     'gross_pnl_usd':gross,'cost_usd':cost,'net_pnl_usd':gross-cost,
                     'leg_gross_pnl_usd':legs,'reconciliation_error_usd':abs(gross-sum(legs.values())),
                     'solver_status':hedge['status'],'time_guard_triggered':hedge.get('time_guard_triggered',False),
                     'termination_reason':hedge.get('termination_reason','not_applicable')})
    return {'session_date':str(utc(snapshot.as_of).date()),'policy_id':episode.policy_id,
            'snapshot_id':snapshot.snapshot_id,'exit_snapshot_id':end.snapshot_id,
            'portfolio_id':portfolio_id(d.portfolio),'settings_id':d.settings.settings_id,
            'vol_version_id':bundle['vol_version_id'],'bundle_id':bundle['bundle_id'],
            'target_id':target.contract_id,'comparisons':rows}


def evaluate_desk_history(episodes, *, bootstrap_samples=500):
    episodes=tuple(sorted(episodes,key=lambda e:utc(e.decision.snapshot.as_of)))
    for e in episodes: _validate_episode(e)
    days=[utc(e.decision.snapshot.as_of).date() for e in episodes]
    if len(days)!=len(set(days)): raise HistoryValidationError('One decision episode per session required')
    for a,b in zip(episodes,episodes[1:]):
        if max(utc(a.exit_snapshot.as_of),utc(a.exit_snapshot.received_at or a.exit_snapshot.as_of))>utc(b.decision.snapshot.as_of):
            raise HistoryValidationError('Overlapping episodes are outside the registered protocol')
    policy_ids={e.policy_id for e in episodes}
    if len(policy_ids)>1: raise HistoryValidationError('Use one predeclared policy; do not switch after seeing outcomes')
    # Targets may change between fresh episodes under a predeclared schedule. Other
    # settings cannot be retuned on the holdout; no identity substitution inside an episode.
    configs={stable_id({k:v for k,v in asdict(e.decision.settings).items() if k!='target_id'}) for e in episodes}
    if len(configs)>1: raise HistoryValidationError('Policy settings changed across historical episodes')
    quantities={e.decision.portfolio[0].quantity for e in episodes}
    if len(quantities)>1: raise HistoryValidationError('Target lot size changed across episodes')
    report={'status':'pending','protocol':'desk_policy_frozen_min80_final40_v1','available_sessions':len(episodes),
            'session_basis':'Distinct UTC decision dates; exchange trading-calendar validation pending',
            'training_sessions':max(0,len(episodes)-40),'holdout_sessions':0,'episodes':[], 'metrics':{},'failures':[],
            'policy_engine':'options_lab.engine.evaluate','real_data_acceptance':'pending_source_verification',
            'limitations':['Paired fresh hedge round trips, not continuous rebalancing.',
                           'Pre-holdout sessions validate data and frozen policy feasibility; this runner does not fit parameters.',
                           'Initial CSO inventory marked at observed mids; hedge entry/exit spreads and fees deducted.',
                           'Observed BBOs do not guarantee execution or depth.',
                           'Raw-source hashes and declared labels do not by themselves authenticate data.',
                           'Sessions use distinct UTC decision dates; exchange trading-calendar acceptance is separate.',
                           'Schedule can change exact target between episodes; expiry crossing is rejected.']}
    if len(episodes)<120:
        report['pending_reasons']=[f'Need {120-len(episodes)} additional decision sessions.']; return report
    holdout=episodes[-40:]; boundary=utc(holdout[0].decision.snapshot.as_of)
    if any(utc(e.policy_declared_at)>=boundary for e in episodes):
        raise HistoryValidationError('All policy and target schedules must be declared before the holdout')
    report['validated_development_sessions']=0
    for i,e in enumerate(episodes):
        segment='development' if i<len(episodes)-40 else 'holdout'
        try:
            result=evaluate_episode(e)
            if segment=='holdout': report['episodes'].append(result)
            else: report['validated_development_sessions']+=1
        except (ValueError,KeyError,OverflowError) as exc:
            report['failures'].append({'snapshot_id':e.decision.snapshot.snapshot_id,'segment':segment,'error':str(exc)})
    report['holdout_sessions']=40
    if report['failures']:
        report.update(status='failed',pending_reasons=['No aggregate metrics: failed paired sessions cannot be silently dropped.'])
        return report
    values={name:[next(r for r in e['comparisons'] if r['strategy']==name)['net_pnl_usd'] for e in report['episodes']]
            for name in ('unhedged','futures_only','futures_vanilla')}
    report['metrics']={name:_metric(v) for name,v in values.items()}
    report['uncertainty']=_paired_bootstrap(values,block_length=5,samples=bootstrap_samples,seed=20260923)
    observed=all(s.mode.startswith('observed') for e in episodes for s in (e.decision.snapshot,e.exit_snapshot))
    report['status']='calculation_complete' if observed else 'engineering_only'
    report['evidence']='Declared observed snapshots; source verification still required' if observed else 'Engineering fixtures; no empirical hedge advantage established'
    report['evaluation_input_sha256']=hashlib.sha256(json.dumps([asdict(e) for e in episodes],sort_keys=True).encode()).hexdigest()
    report['source_hashes']=sorted({v for e in episodes for v in (e.decision.source_sha256,e.exit_source_sha256)})
    return report


def load_episodes(path):
    raw=json.loads(Path(path).read_text())
    result=[]
    for item in raw['episodes']:
        d=item['decision']
        result.append(DeskEpisode(DeskDecision(MarketSnapshot.from_dict(d['snapshot']),
                         tuple(Position(**p) for p in d['portfolio']),Settings.from_dict(d['settings']),
                         d['known_at'],d['source_sha256']),MarketSnapshot.from_dict(item['exit_snapshot']),
                         item['exit_source_sha256'],item['policy_id'],item['policy_declared_at']))
    return tuple(result)


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('input',type=Path); parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(); report=evaluate_desk_history(load_episodes(args.input))
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'status':report['status'],'real_data_acceptance':report['real_data_acceptance'],'output':str(args.output)}))
    raise SystemExit(1 if report['status']=='failed' else 0)
