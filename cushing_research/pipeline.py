"""Build one auditable result shared by every research artifact."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from .features import inventory_features, public_observations, build_events
from .market import validate_market
from .snapshot import verify_snapshot
from .cases import build_cases


SOURCES = [
 {'title':'EIA Weekly Petroleum Status Report archives','url':'https://www.eia.gov/petroleum/supply/weekly/archive/','role':'Publication-dated inventory snapshots'},
 {'title':'EIA Cushing weekly inventory history','url':'https://www.eia.gov/dnav/pet/hist/LeafHandler.ashx?n=PET&s=W_EPC0_SAX_YCUOK_MBBL&f=W','role':'Latest-vintage cross-check, thousand barrels'},
 {'title':'EIA petroleum futures prices','url':'https://www.eia.gov/dnav/pet/pet_pri_fut_s1_d.htm','role':'Public delivery-rank quotes through 2024-04-05; description only'},
 {'title':'EIA publication schedule','url':'https://www.eia.gov/petroleum/supply/weekly/schedule.php','role':'Actual release dates and holiday exceptions'},
 {'title':'NYMEX Light Sweet Crude Oil futures rules','url':'https://www.cmegroup.com/rulebook/NYMEX/2/200.pdf','role':'Contract identity, 1000-barrel multiplier and expiry rules'},
 {'title':'EIA storage capacity report','url':'https://www.eia.gov/petroleum/storagecapacity/','role':'Historical context only; publication discontinued'},
 {'title':'EIA analysis of April 2020 negative WTI prices','url':'https://www.eia.gov/todayinenergy/detail.php?id=43495','role':'Storage commitments and near-expiry market stress'},
 {'title':'EIA 2016 lease-stock methodology change','url':'https://www.eia.gov/todayinenergy/detail.php?id=28292','role':'National inventory scope break and historical backcasting'},
 {'title':'Glencore Commercial Graduate Program','url':'https://job-boards.eu.greenhouse.io/glencoreus/jobs/4912816101','role':'Audience: energy trading, risk, analytics and physical assets'},
]


def json_safe(value):
    if isinstance(value, dict): return {str(k):json_safe(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [json_safe(v) for v in value]
    if isinstance(value, pd.DataFrame): return json_safe(value.to_dict('records'))
    if isinstance(value, (pd.Timestamp,datetime)): return value.isoformat()
    if isinstance(value, np.integer): return int(value)
    if isinstance(value, np.bool_): return bool(value)
    if isinstance(value, (float,np.floating)): return float(value) if np.isfinite(value) else None
    if value is pd.NA or value is pd.NaT: return None
    return value


def save_json(path: Path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(json_safe(value),indent=2,ensure_ascii=False,allow_nan=False)+'\n')


def load_config(root: Path) -> dict:
    return json.loads((root/'config/research.json').read_text())


def descriptive_statistics(observations: pd.DataFrame) -> dict:
    if observations.empty: return {'sample_count':0,'regimes':[],'yearly':[]}
    rows=[]
    for state in ['Low','Normal','High']:
        group=observations[observations.state==state]
        rows.append({'state':state,'n':len(group),
            'median_spread':group.spread.median(), 'q25':group.spread.quantile(.25),
            'q75':group.spread.quantile(.75),'mean_spread':group.spread.mean()})
    yearly=[]
    for year,g in observations.groupby(pd.to_datetime(observations.decision_date).dt.year):
        yearly.append({'year':int(year),'n':len(g),'spearman':g.inv_z.corr(g.spread,method='spearman')})
    corr=observations.inv_z.corr(observations.spread,method='spearman')
    return json_safe({'sample_count':len(observations),'start':observations.decision_date.min(),
        'end':observations.decision_date.max(),'spearman':corr,'regimes':rows,'yearly':yearly,
        'interpretation':'Association between reported inventory state and the preceding available EIA delivery-rank quote. Not a forecast test.',
        'quote_timing':'Most recent public quote strictly before the release date; maximum seven calendar days old',
        'statistical_warning':'Observations are serially dependent. Rank correlation and regime distributions are descriptive, not independent-sample significance tests.'})


def _read_inventory(root: Path):
    path=root/'data/processed/inventory.csv'
    if not path.exists(): raise FileNotFoundError('Inventory snapshot missing. Run fetch before build.')
    df=pd.read_csv(path)
    if df.empty: raise ValueError('Inventory snapshot is empty')
    return df


def _audit_file(root: Path, names: list[str]):
    for name in names:
        p=root/'data/processed'/name
        if p.exists(): return json.loads(p.read_text())
    return {'note':'Source audit not found; see source_url and sha256 in processed input'}


def _save_mechanism_tables(out, mechanism):
    pd.DataFrame(mechanism.get('records',[])).to_csv(out/'mechanism_sample.csv',index=False)
    pd.DataFrame(mechanism.get('exclusions',[]),columns=['decision_date','reason','original_period_index']).to_csv(out/'mechanism_exclusions.csv',index=False)
    coefficients=[]; fits=[]; slopes=[]; deletions=[]; annual=[]
    for name, model in mechanism.get('models',{}).items():
        coefficients += [dict(model=name,**row) for row in model.get('coefficient_table',[])]
        fits.append({'model':name,'status':model.get('status'),'n':model.get('n'),
                     'year_controls':model.get('year_fixed_effects'),
                     'in_sample_mae':model.get('in_sample_mae'),'in_sample_rmse':model.get('in_sample_rmse'),
                     'in_sample_r_squared':model.get('in_sample_r_squared'),
                     'joint_hinge_p_value':model.get('joint_test',{}).get('p_value')})
    for block, sample in mechanism.get('bootstrap',{}).items():
        for name, model in sample.get('models',{}).items():
            for state, row in model.get('slopes',{}).items():
                slopes.append(dict(block_length=int(block),model=name,state=state,**row))
    for row in mechanism.get('leave_one_year_out',[]):
        test=row.get('main_test',{}); model=row.get('models',{}).get('D2',{})
        deletions.append({'excluded_year':row['excluded_year'],'n':row['n'],
                          'test_status':test.get('status'),'joint_hinge_p_value':test.get('p_value'),
                          **{f'{state.lower()}_slope':value.get('estimate') for state,value in model.get('slopes',{}).items()},
                          'hac_gap_policy':row.get('hac_gap_policy')})
    for row in mechanism.get('annual',[]):
        annual.append({**{key:row.get(key) for key in ['year','n','start','end','partial_year','spearman']},
                       **{f'{state.lower()}_n':n for state,n in row.get('state_counts',{}).items()}})
    for filename,rows in [('mechanism_coefficients',coefficients),('mechanism_model_fit',fits),
                          ('mechanism_slopes',slopes),('mechanism_annual',annual),
                          ('mechanism_leave_one_year_out',deletions),('mechanism_curves',mechanism.get('curves',[]))]:
        pd.DataFrame(rows).to_csv(out/f'{filename}.csv',index=False)


def _write_ledger_table(path, ledger, key):
    # Empty trade/attribution tables are valid outcomes and remain readable CSVs.
    empty_columns={
        'daily':['trade_date','gross_pnl','fees','slippage','net_pnl','cumulative_pnl','drawdown','contracts_traded','positions'],
        'trades':['trade_date','contract_id','scenario','model','cost_ticks','force_roundtrip','quantity_change','contracts_traded','old_quantity','new_quantity','side','settlement','execution_price','fees','slippage','phase','closing_quantity','opening_quantity','outgoing_event_id','incoming_event_id'],
        'intervals':['event_id','entry_date','exit_date','near_contract','far_contract','prediction','quantity','signal_threshold','exit_reason','holding_sessions','gross_pnl','fees','slippage','net_pnl','observed_y'],
        'contract_daily':['trade_date','scenario','model','cost_ticks','force_roundtrip','contract_id','start_position','previous_settlement','current_settlement','gross_pnl','quantity_change','contracts_traded','slippage','fees','net_contribution','end_position','start_event_id','end_event_id'],
        'contract_intervals':['event_id','scenario','model','cost_ticks','force_roundtrip','entry_date','exit_date','contract_id','leg','quantity','gross_pnl','fees','slippage','net_pnl','contracts_traded'],
    }
    rows=ledger.get(key,[])
    frame=pd.DataFrame(rows) if rows else pd.DataFrame(columns=empty_columns[key])
    frame.insert(0,'partition',ledger['partition'])
    frame.to_csv(path,index=False)


def run_build(root: Path, config: dict) -> dict:
    out=root/'outputs'; out.mkdir(parents=True,exist_ok=True)
    snapshot_audit=verify_snapshot(root)
    inv=_read_inventory(root)
    inv=inv[(inv.release_date.astype(str)>=config['warmup_start']) &
            (inv.release_date.astype(str)<=config['data_cutoff'])]
    enriched=inventory_features(inv,config)
    enriched.to_csv(out/'inventory_features.csv',index=False)
    fp=root/'data/processed/public_futures.csv'
    futures=pd.read_csv(fp) if fp.exists() else pd.DataFrame()
    observations=public_observations(enriched,futures)
    if len(observations):
        observations=observations[observations.decision_date>=config['train_start']].reset_index(drop=True)
    observations.to_csv(out/'public_descriptive_observations.csv',index=False)
    descriptive=descriptive_statistics(observations)
    from .mechanism import analyze_mechanism
    mechanism_config=json.loads((root/'config/mechanism.json').read_text())
    mechanism_input=observations.merge(enriched[['release_date','report_id','source_url','sha256','knowledge_cutoff',
                                               'inv_reference_n','inv_reference_mean','inv_reference_std']],
                                       left_on='decision_date',right_on='release_date',how='left',validate='one_to_one')
    mechanism=analyze_mechanism(mechanism_input,mechanism_config)
    save_json(out/'mechanism_results.json',mechanism)
    _save_mechanism_tables(out,mechanism)
    frames,market_audit=validate_market(root,config)
    actual_config=config.copy()
    actual_config['market_price_lag_sessions']=0 if market_audit['metadata'].get('same_day_settlement_available') is True else 1
    inv_audit=_audit_file(root,['inventory_audit.json','eia_inventory_audit.json'])
    future_audit=_audit_file(root,['public_futures_audit.json','futures_audit.json'])
    blockers=list(market_audit['errors'])
    if inv_audit.get('status') != 'complete':
        blockers.append('Inventory source audit is incomplete; inspect missing weeks and failed samples')
    result={'schema_version':1,'title':config['title'],'author':config['author'],
        'generated_at':datetime.now(timezone.utc).isoformat(),'data_cutoff':config['data_cutoff'],
        'status':'public_evidence_only', 'config':actual_config, 'inventory':enriched,
        'public_observations':observations,'descriptive':descriptive,'mechanism':mechanism,
        'data_audit':{'inventory':inv_audit,'futures':future_audit,'market':market_audit,'snapshot':snapshot_audit},
        'research':None,'ledgers':[],'blockers':blockers,'sources':SOURCES,
        'coverage':{'inventory_reports':len(inv),'inventory_decision_dates':len(enriched),
            'inventory_start':inv.week_ending.min(),'inventory_end':inv.week_ending.max(),
            'public_quote_days':len(futures),'descriptive_pairs':len(observations),
            'public_quote_end':str(futures.trade_date.max()) if len(futures) else None},
        'limitations':[
          'Individual monthly contract settlements, verified actual expiries and verified settlement calendar have not been supplied. Predictive and trading evidence is pending.',
          'EIA public futures observations are delivery-rank prices through April 5, 2024. Their changes are not actual-contract trading returns.',
          'Inventory features reconstruct knowledge from official publication-period archives; archive files have not been proven immutable since first release.',
          'The seasonal z-score is not physical working-capacity utilization or a measure of uncommitted tank space.',
          'The nationwide inventory control is pending historical definition harmonization. EIA removed lease stocks starting with the week ending October 7, 2016; mixing the original early scope with later values would contaminate the auxiliary test.',
          'Descriptive association can reflect common fundamentals, market anticipation, changing infrastructure and autocorrelation. It does not establish causation or tradable alpha.'
        ],
        'cases':build_cases(enriched,observations)}
    if observations.empty:
        result.update(headline='Inventory reconstruction is available; market evidence remains incomplete.',
                      conclusion='No matched public quotes are available. No predictive or trading conclusion is reported.')
    else:
        regimes={row['state']:row for row in descriptive['regimes']}
        result.update(headline=('Below-seasonal stocks coincide with stronger nearby spreads. The relationship has limits.'
            if descriptive['spearman']<0 else 'The inventory-to-spread relationship needs a closer look. The trading question remains open.'),
            conclusion=f"Across {len(observations):,} publication observations, the median F2-F3 quote is "
            f"{regimes['Low']['median_spread']:+.2f} USD/bbl in low-inventory states and "
            f"{regimes['High']['median_spread']:+.2f} in high-inventory states (rank correlation {descriptive['spearman']:.2f}). "
            'This historical association varies across years. '+str(mechanism.get('conclusion',{}).get('finding',''))+
            ' Actual monthly contract data is still required to test incremental forecasting value and trading costs.')
        if (mechanism.get('main_test',{}).get('status') == 'available'
                and mechanism.get('conclusion',{}).get('category') == 'insufficient_nonlinearity_evidence'):
            result['headline']='Seasonal stocks describe curve states; nonlinear evidence is weak after year controls.'
    if inv_audit.get('status') != 'complete':
        result['limitations'].insert(0,'Inventory archive coverage is incomplete. The displayed description only uses available observations; full predictive evaluation is disabled until the source audit passes.')
    if future_audit.get('status') != 'complete':
        result['blockers'].append('Public futures source audit is incomplete; inspect missing ranks and source coverage')
        result['limitations'].append('The public futures audit is incomplete. Inventory-curve exploration uses only available matched quotes and does not represent the intended complete public sample.')
    if market_audit['passed'] and inv_audit.get('status') == 'complete':
        _full_research(root,frames,enriched,actual_config,result)
    for case in result['cases']:
        if case.get('evidence'):
            pd.DataFrame(case['evidence']).to_csv(out/f"case_{case['id']}_evidence.csv",index=False)
    save_json(out/'case_studies.json',result['cases'])
    manifest=make_manifest(root,actual_config)
    result['run_id']=manifest['run_id']
    result=json_safe(result)
    save_json(out/'results.json',result)
    save_json(out/'run_manifest.json',manifest)
    save_json(out/'data_audit.json',result['data_audit'])
    return result


def _full_research(root,frames,enriched,config,result):
    from .research import run_research
    from .ledger import run_ledger
    c,p,s=frames['contracts.csv'],frames['prices.csv'],frames['sessions.csv']
    events,excluded=build_events(enriched,c,p,s,config)
    accepted_ids=set(events.event_id) if len(events) else set()
    excluded_ids=set(excluded.event_id) if len(excluded) else set()
    expected_ids=set(enriched.report_id.astype(str))
    if (accepted_ids & excluded_ids or accepted_ids | excluded_ids != expected_ids
            or len(events)+len(excluded) != len(enriched)):
        raise ValueError('Every release decision must be accepted or explicitly excluded exactly once')
    events.to_csv(root/'outputs/events.csv',index=False)
    excluded.to_csv(root/'outputs/excluded_events.csv',index=False)
    event_coverage=[]
    for year, annual in enriched.groupby(pd.to_datetime(enriched.release_date).dt.year):
        accepted=events.loc[pd.to_datetime(events.decision_date).dt.year.eq(year)] if len(events) else events
        dropped=excluded.loc[pd.to_datetime(excluded.decision_date).dt.year.eq(year)] if len(excluded) else excluded
        event_coverage.append({'year':int(year),'expected_release_decisions':len(annual),
                              'accepted_events':len(accepted),'excluded_or_pending_events':len(dropped),
                              'exclusion_reasons':dropped.reason.value_counts().to_dict() if len(dropped) else {}})
    result['data_audit']['event_coverage']=event_coverage
    save_json(root/'outputs/event_coverage.json',event_coverage)
    if events.empty or not events.partition.eq('test').any():
        result['blockers'].append('No complete common test events survive the market and feature checks')
        return
    research=run_research(events,s,config)
    result['data_audit']['research_eligibility']=research['metadata']
    predictions=pd.DataFrame(research['predictions'])
    predictions.to_csv(root/'outputs/predictions.csv',index=False)
    if predictions.empty or not predictions.partition.eq('test').any():
        result['blockers'].append('No valid test predictions produced'); return
    main=predictions[predictions.partition=='test']
    common=[set(main.loc[main.model==model,'event_id']) for model in ['B0','B1','B2','B3']]
    years=set(pd.to_datetime(main.decision_date).dt.year)
    if research['metadata']['status']!='complete' or not all(common) or any(ids!=common[0] for ids in common[1:]):
        result['blockers'].append('Main models do not share a complete common test sample or required validation coverage');
        result['data_audit']['research_eligibility']=research['metadata']
        return
    if not set(config['test_years']).issubset(years):
        result['blockers'].append('At least one locked test year has no eligible predictions'); return
    ledgers=[]
    for partition in ['test','recent']:
        part=predictions[predictions.partition==partition]
        for model in ['B1','B2','B3']:
            if not (part.model==model).any(): continue
            for ticks in config['cost_ticks']:
                entry=run_ledger(part,p,s,config,model=model,cost_ticks=ticks)
                entry.update(partition=partition,model=model,cost_ticks=ticks,force_roundtrip=False,scenario='base')
                ledgers.append(entry)
                _write_ledger_table(root/f'outputs/ledger_{partition}_{model}_{ticks}ticks.csv',entry,'daily')
                _write_ledger_table(root/f'outputs/trades_{partition}_{model}_{ticks}ticks.csv',entry,'trades')
            forced=run_ledger(part,p,s,config,model=model,cost_ticks=1,force_roundtrip=True,scenario='forced_roundtrip')
            forced.update(partition=partition,model=model,cost_ticks=1,force_roundtrip=True,scenario='forced_roundtrip')
            ledgers.append(forced)
    for label,rank,delay in [('M3-M4',3,0),('extra_execution_day',2,1)]:
        alt,alt_excluded=build_events(enriched,c,p,s,config,near_rank=rank,execution_delay=delay)
        if alt.empty:
            research['robustness'].append({'name':label,'status':'insufficient_data'});continue
        if label=='extra_execution_day':
            # Execution stress freezes the original forecasts. It is not a retuned strategy.
            forecast_columns=['event_id','model','prediction','alpha']
            ap=alt.merge(predictions[forecast_columns],on='event_id',how='inner',validate='one_to_many')
            ap=ap[ap.partition=='test']
            research['robustness'].append({'name':label,'forecast_policy':'Frozen original forecasts; shifted execution only',
                'common_test_events':int(ap.event_id.nunique())})
            ap.to_csv(root/f'outputs/predictions_{label}.csv',index=False)
            if len(ap):
                delayed=run_ledger(ap,p,s,config,model='B3',cost_ticks=1,scenario=label)
                original_dates=predictions[predictions.model=='B3'][['event_id','entry_date','exit_date']].rename(columns={'entry_date':'original_entry_date','exit_date':'original_exit_date'})
                delayed['execution_dates']=ap[ap.model=='B3'][['event_id','entry_date','exit_date']].merge(original_dates,on='event_id',validate='one_to_one').to_dict('records')
                delayed.update(partition='test',model='B3',cost_ticks=1,force_roundtrip=False,scenario=label)
                ledgers.append(delayed)
        else:
            alternate=run_research(alt,s,config)
            research['robustness'].append({'name':label,'status':alternate['metadata']['status'],
                'validation_coverage':alternate['metadata'].get('validation_coverage'),
                'test_year_coverage':alternate['metadata'].get('test_year_coverage'),
                'metrics':alternate['metrics'],'comparisons':alternate['comparisons']})
            pd.DataFrame(alternate['predictions']).to_csv(root/f'outputs/predictions_{label}.csv',index=False)
    for ledger in ledgers:
        name=f"{ledger['partition']}_{ledger['model']}_{ledger['cost_ticks']}ticks_{ledger['scenario']}"
        for key in ['daily','trades','intervals','contract_daily','contract_intervals']:
            _write_ledger_table(root/f'outputs/{key}_{name}.csv',ledger,key)
    result.update(status='full_research',research=research,ledgers=ledgers,blockers=[])
    result['limitations'][0]='Execution is simulated at future daily settlement plus assumed fees and slippage. Intraday executable quotes and historical margin requirements are unavailable.'
    primary=next(row for row in research['comparisons'] if row.get('primary') and row['partition']=='test')
    supported=primary.get('evidence')=='improvement'
    result['headline']=('Inventory improves the locked out-of-sample forecast comparison.' if supported
        else 'The locked test does not establish an inventory forecasting advantage.')
    interval=(f"95% moving-block interval [{primary['ci_low']:.4f}, {primary['ci_high']:.4f}]"
        if primary.get('ci_low') is not None else 'interval unavailable because the paired sample is too small')
    result['conclusion']=(f"Across {primary['n']} paired events, B3 versus B1 MAE improvement is "
        f"{primary['mae_improvement']:.4f} USD/bbl ({interval}). "
        'Read this forecast-loss comparison together with the fixed-position, cost-adjusted dollar ledger; '
        'a statistical improvement and economically useful performance are separate findings.')
    result['cases']=build_cases(enriched,pd.DataFrame(result['public_observations']),research,ledgers)


def make_manifest(root: Path, config: dict) -> dict:
    files={}
    for folder in ['cushing_research','config','data/processed','data/input']:
        for path in sorted((root/folder).rglob('*')):
            if path.is_file() and '__pycache__' not in str(path) and path.suffix in ['.py','.json','.csv']:
                files[str(path.relative_to(root))]=hashlib.sha256(path.read_bytes()).hexdigest()
    versions={}
    for name in ['numpy','pandas','scikit-learn','scipy','matplotlib','plotly','reportlab']:
        try: versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name]='unavailable'
    stable={'files':files,'config':config,'versions':versions,'python':platform.python_version()}
    digest=hashlib.sha256(json.dumps(stable,sort_keys=True).encode()).hexdigest()
    return {**stable,'run_id':digest[:16],'created_at':datetime.now(timezone.utc).isoformat()}
