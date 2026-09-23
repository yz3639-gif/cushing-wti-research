from copy import deepcopy
from hashlib import sha256
import json
import pytest
from options_lab import validate


def good_report():
    return {
        'tests': {'status': 'passed'},
        'performance': {'updates': 1000, 'contracts': 20, 'scenarios': 100,
                        'p95_seconds': .1, 'failure_count': 0,
                        'distinct_input_states': 1000},
        'invariants': {'negative_or_crossed_quotes': 0, 'mixed_versions': 0,
                       'constraint_violations': 0, 'unavailable_bundles': 0,
                       'max_leg_portfolio_reconciliation_usd': 0.0},
        'original_research': {'status': 'passed'},
    }


@pytest.mark.parametrize('section,key,value', [
    ('tests','status','not_run'),
    ('performance','p95_seconds',2.01),
    ('performance','p95_seconds',float('nan')),
    ('performance','p95_seconds',-1),
    ('performance','updates',float('nan')),
    ('performance','distinct_input_states',float('nan')),
    ('performance','failure_count',1),
    ('performance','updates',999),
    ('performance','distinct_input_states',1),
    ('invariants','negative_or_crossed_quotes',1),
    ('invariants','mixed_versions',1),
    ('invariants','constraint_violations',1),
    ('invariants','unavailable_bundles',1),
    ('invariants','max_leg_portfolio_reconciliation_usd',.011),
    ('original_research','status','pending'),
])
def test_every_hard_gate_blocks_acceptance(section,key,value):
    report=deepcopy(good_report()); report[section][key]=value
    assert validate.acceptance_errors(report)


def test_all_engineering_gates_can_pass_without_claiming_real_data():
    report=good_report()
    report['live_acceptance']={'status':'pending'}
    report['real_snapshot_acceptance']={'status':'pending'}
    assert validate.acceptance_errors(report)==[]


def test_clean_clone_uses_public_preservation_baseline_and_detects_changes(tmp_path,monkeypatch):
    monkeypatch.setattr(validate,'ROOT',tmp_path)
    original=tmp_path/'research.txt'; original.write_text('preserved research')
    path=tmp_path/'options_lab/examples/research_baseline.json'; path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'research.txt':sha256(original.read_bytes()).hexdigest()}))
    result=validate.guard_original()
    assert result['status']=='passed' and result['checked_files']==1
    assert result['baseline_source']=='options_lab/examples/research_baseline.json'
    original.write_text('unexpected change')
    assert validate.guard_original()['changed_files']==['research.txt']


def test_missing_preservation_baseline_still_blocks_acceptance(tmp_path,monkeypatch):
    monkeypatch.setattr(validate,'ROOT',tmp_path)
    report=good_report(); report['original_research']=validate.guard_original()
    assert report['original_research']['status']=='pending'
    assert validate.acceptance_errors(report)


def test_benchmark_changes_actual_quotes_not_only_sequence():
    from options_lab.adapters import engineering_fixture
    from options_lab.volatility import calibrate_market,validate_version
    initial=engineering_fixture().snapshots[0]
    states=[validate.benchmark_snapshot(initial,i) for i in (1,2,3,1000)]
    assert len({s.quote_map[s.contracts[0].contract_id].mid for s in states})==4
    for state in states:
        assert state.mode=='engineering_fixture'
        assert not validate_version(calibrate_market(state),state)
    assert states[1].as_of>states[0].as_of


@pytest.mark.parametrize('mutation', ['missing_quotes','negative_ask','prohibited_cso','net_risk','missing_cost','position_report','quote_mixed'])
def test_mutated_bundle_cannot_pass_independent_checks(mutation):
    from tests_options.test_engine import desk_fixture
    from options_lab.engine import evaluate
    s,v,p,c=desk_fixture(); b=deepcopy(evaluate(s,v,p,c))
    if mutation=='missing_quotes': b['quotes']=[]
    elif mutation=='negative_ask': b['quotes'][0].update(bid=None,ask=-1,bid_size=0,ask_size=0)
    elif mutation=='prohibited_cso': b['hedges']['proxy'].update(trades=[{'contract_id':c.target_id,'quantity':1}],objective=0,status='optimal')
    elif mutation=='net_risk': b['hedges']['proxy'].update(net_pnl=[1e9]*c.scenario_count,objective=0)
    elif mutation=='missing_cost': b['hedges']['proxy']['cost']=-1
    elif mutation=='position_report': b['hedges']['proxy']['position_constraints']['compliant']=False
    elif mutation=='quote_mixed': b['quotes'][0]['contract_id']='UNKNOWN'
    assert any(validate.bundle_invariants(b,s,v,p,c))
