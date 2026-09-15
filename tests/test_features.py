import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cushing_research.features import inventory_features, public_observations, build_events


CONFIG=json.loads((Path(__file__).parents[1]/'config/research.json').read_text())


def inv_fixture():
    rows=[]
    for i,day in enumerate(pd.date_range('2011-08-05','2020-06-26',freq='W-FRI')):
        release=day+pd.Timedelta(days=5)
        value=35+7*np.sin(i/8)+i*.01
        rows.append({'report_id':f'test-{i}','week_ending':str(day.date()),'release_date':str(release.date()),
            'knowledge_cutoff':(release+pd.Timedelta(hours=23,minutes=59,seconds=59)).tz_localize('America/New_York').tz_convert('UTC').isoformat(),
            'cushing_mbbl':value,'us_mbbl':400+value+i*.1,'source_url':'test-fixture-only','sha256':'fixture','timestamp_quality':'fixture'})
    return pd.DataFrame(rows)


def test_future_inventory_changes_cannot_change_past_features():
    inv=inv_fixture(); before=inventory_features(inv,CONFIG)
    inv.loc[inv.release_date>'2018-12-31',['cushing_mbbl','us_mbbl']]=10000
    after=inventory_features(inv,CONFIG)
    pd.testing.assert_frame_equal(before[before.release_date<='2018-12-31'].reset_index(drop=True),
                                  after[after.release_date<='2018-12-31'].reset_index(drop=True))


def test_later_revision_cannot_replace_original_observation():
    inv=inv_fixture(); revised=inv.iloc[[200]].copy()
    revised['knowledge_cutoff']='2020-07-15T03:59:59+00:00'
    revised['report_id']='later-revision';revised['cushing_mbbl']=999
    expected=inventory_features(inv,CONFIG)
    actual=inventory_features(pd.concat([inv,revised],ignore_index=True),CONFIG)
    pd.testing.assert_frame_equal(expected,actual)


def test_two_weeks_same_release_one_decision_and_one_week_delta():
    inv=inv_fixture(); inv.loc[201,'knowledge_cutoff']=inv.loc[202,'knowledge_cutoff']
    inv.loc[201,'release_date']=inv.loc[202,'release_date']
    result=inventory_features(inv,CONFIG)
    observed=result[result.report_id=='test-202'].iloc[0]
    assert observed.reports_on_release_date==2
    assert observed.inv_delta1==pytest.approx(inv.loc[202,'cushing_mbbl']-inv.loc[201,'cushing_mbbl'])
    assert len(result)==len(inv)-1


def test_z_requires_history_and_uses_prior_calendar_years():
    result=inventory_features(inv_fixture(),CONFIG)
    assert pd.isna(result.iloc[0].inv_z)
    r=result[result.release_date=='2015-04-22'].iloc[0]
    assert r.inv_reference_n>=20
    assert r.inv_z==pytest.approx((r.cushing_mbbl-r.inv_reference_mean)/r.inv_reference_std)


def test_public_quotes_are_earlier_and_not_extended_to_latest_inventory():
    inv=inventory_features(inv_fixture(),CONFIG)
    prices=pd.DataFrame({'trade_date':['2017-01-03','2017-01-04','2017-01-05'],
        'F2':[50.,51.,52.],'F3':[51.,52.,53.]})
    result=public_observations(inv,prices)
    assert len(result)==1
    assert result.iloc[0].price_date=='2017-01-03'
    assert result.iloc[0].decision_date=='2017-01-04'
    assert result.iloc[0].spread==-1


def market_fixture():
    days=pd.bdate_range('2021-11-01','2022-04-29')
    # Test-only weekday calendar; this is not a supplied real CME calendar.
    sessions=pd.DataFrame({'trade_date':days.strftime('%Y-%m-%d'),
        'settlement_time':[(d+pd.Timedelta(hours=14,minutes=30)).tz_localize('America/New_York').isoformat() for d in days]})
    contracts=pd.DataFrame({'contract_id':['CL-2022-02','CL-2022-03','CL-2022-04','CL-2022-05'],
        'delivery_month':['2022-02','2022-03','2022-04','2022-05'],
        'last_trade_date':['2022-01-20','2022-02-22','2022-03-22','2022-04-20']})
    prices=[]
    for i,d in enumerate(days):
        for j,c in enumerate(contracts.contract_id):
            prices.append({'trade_date':str(d.date()),'contract_id':c,'settlement':-10+j*3+i*(j+1)*.01})
    records=[]
    for i,release in enumerate(['2022-01-12','2022-01-19','2022-01-26']):
        records.append({'report_id':f'test-{i}','release_date':release,
            'knowledge_cutoff':(pd.Timestamp(release)+pd.Timedelta(hours=23,minutes=59,seconds=59)).tz_localize('America/New_York').isoformat(),
            'week_ending':str((pd.Timestamp(release)-pd.Timedelta(days=5)).date()),'cushing_mbbl':35.,'state':'Normal',
            'inv_z':.2,'inv_delta1':1.,'inv_delta4':2.,'national_z':.1,'national_delta1':2.,'national_delta4':3.,
            'low_hinge':0.,'high_hinge':0.,'inv_interaction':.2})
    return pd.DataFrame(records),contracts,pd.DataFrame(prices),sessions


def test_labels_keep_original_contract_pair_and_negative_prices():
    inventory,contracts,prices,sessions=market_fixture()
    events,_=build_events(inventory,contracts,prices,sessions,CONFIG)
    event=events.iloc[0]
    assert event.near_contract=='CL-2022-03'
    assert event.far_contract=='CL-2022-04'
    assert event.entry_date=='2022-01-13'
    assert event.known_price_date=='2022-01-11'
    panel=prices.pivot(index='trade_date',columns='contract_id',values='settlement')
    spread=panel[event.near_contract]-panel[event.far_contract]
    assert event.y==pytest.approx(spread.loc[event.exit_date]-spread.loc[event.entry_date])


def test_missing_held_price_stops_backtest():
    inventory,contracts,prices,sessions=market_fixture()
    prices=prices[~((prices.trade_date=='2022-01-14')&(prices.contract_id=='CL-2022-03'))]
    with pytest.raises(ValueError,match='Missing held-contract settlement'):
        build_events(inventory,contracts,prices,sessions,CONFIG)


def test_future_prices_change_label_but_not_features():
    inventory,contracts,prices,sessions=market_fixture()
    before,_=build_events(inventory,contracts,prices,sessions,CONFIG)
    prices.loc[(prices.trade_date>'2022-01-12')&(prices.contract_id=='CL-2022-03'),'settlement']+=100
    after,_=build_events(inventory,contracts,prices,sessions,CONFIG)
    keys=['spread','delta_spread_5','vol_spread_20','dte','near_contract','far_contract']
    assert before.iloc[0][keys].to_dict()==after.iloc[0][keys].to_dict()


def test_max_holding_cap_and_partition_boundary():
    inventory,contracts,prices,sessions=market_fixture()
    events,_=build_events(inventory.iloc[[0]],contracts,prices,sessions,CONFIG)
    assert events.iloc[0].holding_sessions==10
    assert events.iloc[0].exit_reason=='holding_cap'


def test_release_and_complete_target_must_stay_in_same_partition():
    inventory,contracts,prices,sessions=market_fixture()
    inventory=inventory.iloc[[0]].copy()
    inventory['release_date']='2021-12-31'
    inventory['knowledge_cutoff']='2021-12-31T23:59:59-05:00'
    events,excluded=build_events(inventory,contracts,prices,sessions,CONFIG)
    assert events.empty
    assert excluded.reason.str.contains('partition boundary').all()


def test_national_scope_change_does_not_create_inventory_draw_signal():
    result=inventory_features(inv_fixture(),CONFIG)
    boundary=result[result.week_ending=='2016-10-07'].iloc[0]
    assert pd.isna(boundary.national_delta1)
    assert pd.isna(boundary.national_z)
    assert pd.notna(boundary.inv_z)
    assert boundary.national_definition=='excludes_lease_stocks'


def test_missing_auxiliary_national_features_do_not_drop_main_events():
    inventory,contracts,prices,sessions=market_fixture()
    inventory[['national_z','national_delta1','national_delta4']]=np.nan
    events,_=build_events(inventory,contracts,prices,sessions,CONFIG)
    assert len(events)>0
    assert events.national_z.isna().all()
