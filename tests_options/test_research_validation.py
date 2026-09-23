from dataclasses import replace
from datetime import datetime, timedelta, timezone
import pytest
from options_lab.research_validation import (
    HedgeMark, HistoryObservation, HistoryValidationError, evaluate_history,
)


def observations(count=120):
    # Numerical unit-test inputs, never exported or presented as real observations.
    base=datetime(2025,1,1,14,tzinfo=timezone.utc)
    return [HistoryObservation(str(i),(base+timedelta(days=i)).date(),base+timedelta(days=i),
            base+timedelta(days=i),base+timedelta(days=i,hours=1),f's{i}',f'n{i}',
            'unit-test declared-source', 'a'*64,'observed','CSO-TEST',1,1000.,2.,3.,'fixed-fees-v1',
            (HedgeMark('CL-TEST','future',1000.,79.,80.,.01,1.),
             HedgeMark('LCE-TEST','vanilla',1000.,4.,4.5,.02,1.))) for i in range(count)]


def futures_trainer(train):
    assert len(train)==80
    def strategy(decision):
        assert not hasattr(decision,'next_target_mark')
        assert not hasattr(decision.hedges[0],'next_mark')
        return {'CL-TEST':-1}
    return strategy


def combined_trainer(train):
    assert len(train)==80
    return lambda decision: {'LCE-TEST':-2}


def test_insufficient_history_is_pending_and_does_not_fit():
    report=evaluate_history(observations(3),futures_only_trainer=lambda _: pytest.fail('fit called'),futures_vanilla_trainer=combined_trainer)
    assert report['status']=='pending' and report['available_sessions']==3
    assert report['metrics']=={} and report['episodes']==[]
    assert '117' in report['pending_reasons'][0]


def test_paired_holdout_uses_same_observations_and_actual_cost_formula():
    report=evaluate_history(observations(),futures_only_trainer=futures_trainer,futures_vanilla_trainer=combined_trainer)
    assert report['status']=='complete' and len(report['episodes'])==120
    by_strategy={name:[r for r in report['episodes'] if r['strategy']==name] for name in report['strategies']}
    assert all([r['observation_id'] for r in rows]==[str(i) for i in range(80,120)] for rows in by_strategy.values())
    assert by_strategy['unhedged'][0]['net_pnl_usd']==1000.
    assert by_strategy['futures_only'][0]['gross_pnl_usd']==0.
    assert by_strategy['futures_only'][0]['cost_usd']==22.
    assert by_strategy['futures_vanilla'][0]['cost_usd']==84.
    assert all(r['net_pnl_usd']==r['gross_pnl_usd']-r['cost_usd'] for r in report['episodes'])


def test_fixture_history_cannot_claim_real_data_completion():
    data=[replace(o,data_kind='test_fixture') for o in observations()]
    report=evaluate_history(data,futures_only_trainer=futures_trainer,futures_vanilla_trainer=combined_trainer)
    assert report['status']=='pending' and not report['metrics']


def test_future_information_and_training_overlap_are_rejected():
    data=observations();data[0]=replace(data[0],known_at=data[0].decision_at+timedelta(seconds=1))
    with pytest.raises(HistoryValidationError,match='future information'):
        evaluate_history(data)
    data=observations();data[79]=replace(data[79],realized_at=data[80].decision_at+timedelta(seconds=1))
    with pytest.raises(HistoryValidationError,match='training outcomes'):
        evaluate_history(data,futures_only_trainer=futures_trainer,futures_vanilla_trainer=combined_trainer)


def test_integer_and_baseline_constraints():
    with pytest.raises(HistoryValidationError,match='integer'):
        evaluate_history(observations(),futures_only_trainer=lambda _:lambda _: {'CL-TEST':.5},futures_vanilla_trainer=combined_trainer)
    with pytest.raises(HistoryValidationError,match='selected a vanilla'):
        evaluate_history(observations(),futures_only_trainer=combined_trainer,futures_vanilla_trainer=combined_trainer)


def test_duplicate_sessions_and_unavailable_instruments_rejected():
    data=observations();data[1]=replace(data[1],session_date=data[0].session_date)
    with pytest.raises(HistoryValidationError,match='one observation'):
        evaluate_history(data)
    with pytest.raises(HistoryValidationError,match='unavailable'):
        evaluate_history(observations(),futures_only_trainer=lambda _:lambda _: {'UNKNOWN':1},futures_vanilla_trainer=combined_trainer)


def test_all_prior_sessions_train_and_final40_holdout_default_benchmarks():
    report=evaluate_history(observations(135),bootstrap_samples=100)
    assert report['status']=='complete'
    assert report['training_sessions']==95 and report['holdout_sessions']==40
    assert report['episodes'][0]['observation_id']=='95'
    assert report['ignored_sessions']==0
    again=evaluate_history(observations(135),bootstrap_samples=100)
    assert report['uncertainty']==again['uncertainty']
    assert report['uncertainty']['block_length']==5
    assert report['uncertainty']['comparisons']['futures_only_minus_unhedged']['mean_net_pnl_difference_ci95_usd']


def test_default_benchmarks_reject_unmapped_contract_rollover():
    data=observations()
    data[-1]=replace(data[-1],hedges=(replace(data[-1].hedges[0],contract_id='NEW_EXPIRY'),data[-1].hedges[1]))
    with pytest.raises(HistoryValidationError,match='rollover'):
        evaluate_history(data)
