from dataclasses import replace, asdict
from datetime import timedelta
import json
import math
import pytest
from options_lab.desk_history import (
    DeskDecision, DeskEpisode, decision_bundle, evaluate_episode,
    evaluate_desk_history, load_episodes,
)
from options_lab.models import utc
from options_lab.pricing import instrument_value
from options_lab.research_validation import HistoryValidationError
from tests_options.test_engine import desk_fixture


def history(count=120):
    snapshot,_,portfolio,settings=desk_fixture()
    start=utc(snapshot.as_of)
    contracts=tuple(replace(c,source='Synthetic history test',expiry=(utc(c.expiry)+timedelta(days=330)).isoformat()) for c in snapshot.contracts)
    settings=replace(settings,risk_limit_dollars=1e9,hedge_node_limit=1,scenario_count=7)
    def state(i):
        at=(start+timedelta(days=i)).isoformat()
        qs=tuple(replace(q,as_of=at,bid=q.bid+.1*math.sin(i*.2),ask=q.ask+.1*math.sin(i*.2))
                 for q in snapshot.quotes if snapshot.contract_map[q.contract_id].kind=='future')
        m=replace(snapshot,as_of=at,contracts=contracts,quotes=qs,sequence=i)
        options=[]
        for c in contracts:
            if c.kind=='future': continue
            vol=(3. if c.kind=='cso' else .3)*(1+.03*math.sin(i*.1))
            mid=instrument_value(c,m,vol)
            original=snapshot.quote_map[c.contract_id]
            options.append(replace(original,as_of=at,bid=mid-.01,ask=mid+.01))
        return replace(m,quotes=qs+tuple(options))
    states=[state(i) for i in range(count+1)]
    return [DeskEpisode(DeskDecision(states[i],portfolio,settings,states[i].as_of,'a'*64),
                        states[i+1],'b'*64,'frozen-test-policy',start.isoformat()) for i in range(count)]


def test_actual_desk_trades_are_used_and_entry_exit_costs_reconcile():
    episode=history(1)[0]
    bundle=decision_bundle(episode.decision)
    result=evaluate_episode(episode)
    for row,key in zip(result['comparisons'],(None,'delta','proxy')):
        expected={} if key is None else {t['contract_id']:t['quantity'] for t in bundle['hedges'][key]['trades']}
        assert row['holdings']==expected
        cost=sum(abs(q)*22. for q in expected.values())
        assert row['cost_usd']==pytest.approx(cost)
        assert row['net_pnl_usd']==pytest.approx(row['gross_pnl_usd']-cost)
        assert row['reconciliation_error_usd']<.01
    # Realized target move affects P&L, never changes selected hedge quantities.
    end=episode.exit_snapshot
    changed=replace(end,quotes=tuple(replace(q,bid=q.bid+1,ask=q.ask+1) if q.contract_id==episode.decision.settings.target_id else q for q in end.quotes))
    other=evaluate_episode(replace(episode,exit_snapshot=changed))
    assert [r['holdings'] for r in other['comparisons']]==[r['holdings'] for r in result['comparisons']]
    assert other['comparisons'][0]['gross_pnl_usd']!=result['comparisons'][0]['gross_pnl_usd']


def test_short_sample_pending_and_fixture_final40_never_claims_empirical_completion():
    assert evaluate_desk_history(history(3))['status']=='pending'
    data=history()
    result=evaluate_desk_history(data,bootstrap_samples=100)
    assert result['status']=='engineering_only'
    assert result['training_sessions']==80 and result['holdout_sessions']==40
    assert len(result['episodes'])==40 and not result['failures']
    assert result['episodes'][0]['snapshot_id']==data[80].decision.snapshot.snapshot_id
    assert result['real_data_acceptance']=='pending_source_verification'
    assert result['uncertainty']['comparisons']


def test_future_known_time_and_holdout_retuning_rejected():
    data=history(1); d=data[0].decision
    with pytest.raises(HistoryValidationError,match='known after'):
        evaluate_episode(replace(data[0],decision=replace(d,known_at=data[0].exit_snapshot.as_of)))
    late=replace(d.snapshot,received_at=(utc(d.snapshot.as_of)+timedelta(seconds=1)).isoformat())
    with pytest.raises(HistoryValidationError,match='before snapshot received'):
        evaluate_episode(replace(data[0],decision=replace(d,snapshot=late)))
    data=history(); data[-1]=replace(data[-1],decision=replace(data[-1].decision,settings=replace(data[-1].decision.settings,risk_aversion=.99)))
    with pytest.raises(HistoryValidationError,match='settings changed'):
        evaluate_desk_history(data)
    data=history(); data[-1]=replace(data[-1],policy_declared_at=data[80].decision.snapshot.as_of)
    with pytest.raises(HistoryValidationError,match='before the holdout'):
        evaluate_desk_history(data)


def test_missing_exit_mark_fails_whole_paired_study_without_dropping_day():
    data=history(); end=data[-1].exit_snapshot
    data[-1]=replace(data[-1],exit_snapshot=replace(end,quotes=tuple(q for q in end.quotes if q.contract_id!=data[-1].decision.settings.target_id)))
    result=evaluate_desk_history(data,bootstrap_samples=100)
    assert result['status']=='failed' and len(result['failures'])==1
    assert result['metrics']=={}


def test_empty_development_session_cannot_count_toward_valid120():
    data=history(); d=data[0].decision
    data[0]=replace(data[0],decision=replace(d,snapshot=replace(d.snapshot,quotes=())))
    result=evaluate_desk_history(data,bootstrap_samples=100)
    assert result['status']=='failed' and result['validated_development_sessions']==79
    assert result['failures'][0]['segment']=='development'
    assert result['metrics']=={}


def test_contract_identity_and_expiry_policy_are_not_silently_substituted():
    e=history(1)[0]; end=e.exit_snapshot
    changed=replace(end,contracts=tuple(replace(c,strike=c.strike+.1) if c.contract_id==e.decision.settings.target_id else c for c in end.contracts))
    with pytest.raises(HistoryValidationError,match='identity'):
        evaluate_episode(replace(e,exit_snapshot=changed))
    from options_lab.desk_history import _observed_pair
    for contract in e.decision.snapshot.contracts:
        expired=replace(contract,expiry=(utc(e.decision.snapshot.as_of)+timedelta(hours=1)).isoformat())
        with pytest.raises(HistoryValidationError,match='settlement policy'):
            _observed_pair(expired,e.decision.snapshot,end,e.decision.settings,executable=True)


def test_json_roundtrip_preserves_exact_engine_inputs(tmp_path):
    data=history(1); p=tmp_path/'history.json'
    p.write_text(json.dumps({'episodes':[asdict(e) for e in data]}))
    restored=load_episodes(p)
    assert restored==tuple(data)
    assert evaluate_episode(restored[0])==evaluate_episode(data[0])
