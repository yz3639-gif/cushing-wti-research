"""Artificial market paths verify module interfaces; never populate real outputs."""
from pathlib import Path
import runpy

import pandas as pd
from pypdf import PdfReader

from cushing_research.research import run_research
from cushing_research.ledger import run_ledger
from cushing_research.report import render_report
from cushing_research.cases import build_cases
from cushing_research import pipeline


def test_research_ledger_and_four_page_report_share_schema(tmp_path):
    helpers=runpy.run_path(str(Path(__file__).with_name('test_research.py')))
    events,prices,sessions,config=helpers['synthetic_research_and_prices']()
    research=run_research(events,sessions,config)
    predictions=pd.DataFrame(research['predictions'])
    test=predictions[predictions.partition=='test']
    ledgers=[]
    for model in ['B1','B3']:
        for ticks in [1,2,4]:
            ledger=run_ledger(test,prices,sessions,config,model=model,cost_ticks=ticks)
            ledger.update(partition='test',model=model,cost_ticks=ticks,force_roundtrip=False)
            ledgers.append(ledger)
    result={'status':'full_research','title':'SYNTHETIC SOFTWARE TEST ONLY',
        'author':'SYNTHETIC SOFTWARE TEST','data_cutoff':'2026-06-30',
        'run_id':'synthetic-interface-test','generated_at':'2026-09-15T00:00:00Z',
        'headline':'SYNTHETIC FIXTURE: NOT RESEARCH EVIDENCE',
        'conclusion':'Artificial market paths test the interfaces only.',
        'inventory':[],'public_observations':[],'descriptive':{},
        'research':research,'ledgers':ledgers,'cases':build_cases(pd.DataFrame(),pd.DataFrame(),research,ledgers),
        'config':config,'sources':[],'limitations':['Synthetic test only.']}
    files=render_report(result,tmp_path)
    assert len(PdfReader(files['pdf']).pages)==4
    assert Path(files['html']).exists()
    assert Path(files['interview_notes']).exists()
    assert all(abs(sum(d['net_pnl'] for d in ledger['daily'])-ledger['summary']['net_pnl'])<1e-7 for ledger in ledgers)
    content=Path(files['html']).read_text()
    assert 'synthetic-interface-test' in content
    assert 'SYNTHETIC FIXTURE' in content


def test_full_pipeline_exports_attribution_and_keeps_delayed_forecasts_frozen(tmp_path,monkeypatch):
    helpers=runpy.run_path(str(Path(__file__).with_name('test_research.py')))
    events,prices,sessions,config=helpers['synthetic_research_and_prices']()
    config.update(cost_ticks=[1,2,4],data_cutoff='2026-09-30')
    inventory=events.rename(columns={'event_id':'report_id','decision_date':'release_date'}).copy()
    inventory['week_ending']=inventory.release_date
    inventory['knowledge_cutoff']=inventory.cutoff
    inventory['cushing_mbbl']=30+inventory.inv_z
    inventory['source_url']='https://example.invalid/synthetic-test-only'
    inventory['sha256']='0'*64
    inventory['state']='Normal'
    lookup=prices.set_index(['trade_date','contract_id']).settlement
    def supplied_events(*args,near_rank=2,execution_delay=0,**kwargs):
        frame=events.copy()
        if execution_delay:
            for column in ['entry_date','exit_date']:
                frame[column]=(pd.to_datetime(frame[column])+pd.offsets.BDay(execution_delay)).dt.strftime('%Y-%m-%d')
            def target(row):
                spread=lambda day:lookup[day,row.near_contract]-lookup[day,row.far_contract]
                return spread(row.exit_date)-spread(row.entry_date)
            frame['y']=frame.apply(target,axis=1)
        return frame,pd.DataFrame(columns=['event_id','decision_date','reason','status'])
    monkeypatch.setattr(pipeline,'build_events',supplied_events)
    (tmp_path/'outputs').mkdir()
    result={'data_audit':{},'blockers':[],'public_observations':[],
            'limitations':['Synthetic execution only'],'cases':[]}
    pipeline._full_research(tmp_path,{'contracts.csv':pd.DataFrame(),'prices.csv':prices,'sessions.csv':sessions},inventory,config,result)
    assert result['status']=='full_research'
    delayed=next(l for l in result['ledgers'] if l.get('scenario')=='extra_execution_day')
    base=next(l for l in result['ledgers'] if l['partition']=='test' and l['model']=='B3' and l['cost_ticks']==1 and l['scenario']=='base')
    assert {r['event_id']:r['prediction'] for r in delayed['intervals']}=={r['event_id']:r['prediction'] for r in base['intervals']}
    assert all(d['entry_date']>d['original_entry_date'] for d in delayed['execution_dates'])
    assert all(r['scenario']=='extra_execution_day' for r in delayed['contract_daily'])
    exported=pd.read_csv(tmp_path/'outputs/contract_daily_test_B3_1ticks_extra_execution_day.csv')
    assert abs(exported.net_contribution.sum()-delayed['summary']['net_pnl'])<1e-7
    assert any(c['id']=='failure' and 'position' in c for c in result['cases'])
    assert all(r['expected_release_decisions']==r['accepted_events']+r['excluded_or_pending_events'] for r in result['data_audit']['event_coverage'])
