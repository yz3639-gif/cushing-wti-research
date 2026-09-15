"""Features from published observations and fixed, dated futures contracts."""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from datetime import date

import numpy as np
import pandas as pd


def _season_day(value) -> int:
    ts = pd.Timestamp(value)
    return date(2000, ts.month, ts.day).timetuple().tm_yday


def inventory_features(inventory: pd.DataFrame, config: dict) -> pd.DataFrame:
    """One point-in-time feature record per actual publication day."""
    if inventory.empty:
        return inventory.copy()
    frame = inventory.copy()
    frame['week_ending'] = pd.to_datetime(frame.week_ending)
    frame['_available'] = pd.to_datetime(frame.knowledge_cutoff, utc=True)
    frame = frame.sort_values(['_available', 'week_ending', 'report_id'])
    frame = frame.drop_duplicates('week_ending', keep='first')
    frame['national_mbbl'] = frame.us_mbbl - frame.cushing_mbbl
    frame['_season_day'] = frame.week_ending.map(_season_day)
    result = []
    for available, releases in frame.groupby('_available', sort=True):
        current = releases.sort_values('week_ending').iloc[-1]
        ref = current.week_ending
        known = frame[(frame._available <= available) & (frame.week_ending <= ref)]
        years = int(config.get('seasonal_years', 3))
        seasonal = known[(known.week_ending.dt.year >= ref.year-years)
                         & (known.week_ending.dt.year < ref.year)]
        dist = (seasonal._season_day - _season_day(ref)).abs()
        seasonal = seasonal[np.minimum(dist, 366-dist) <= config.get('seasonal_day_window', 28)]
        row = current.drop(labels=['_available', '_season_day']).to_dict()
        row['week_ending'] = ref.strftime('%Y-%m-%d')
        row['release_date'] = available.tz_convert('America/New_York').strftime('%Y-%m-%d')
        row['knowledge_cutoff'] = available.isoformat()
        row['reports_on_release_date'] = int(len(releases))
        for prefix, col in [('inv', 'cushing_mbbl'), ('national', 'national_mbbl')]:
            scope_seasonal=seasonal
            scope_known=known
            if prefix=='national':
                boundary=pd.Timestamp(config.get('national_definition_break_week','2016-10-07'))
                current_scope=ref>=boundary
                scope_seasonal=seasonal[(seasonal.week_ending>=boundary)==current_scope]
                scope_known=known[(known.week_ending>=boundary)==current_scope]
                row['national_definition']='excludes_lease_stocks' if current_scope else 'includes_lease_stocks'
            reference = pd.to_numeric(scope_seasonal[col], errors='coerce').dropna()
            sd = reference.std(ddof=1)
            enough = len(reference) >= config.get('seasonal_min_observations', 20)
            row[prefix+'_z'] = (float(current[col])-reference.mean())/sd if enough and sd > 1e-12 else np.nan
            row[prefix+'_reference_n'] = int(len(reference))
            row[prefix+'_reference_mean'] = float(reference.mean()) if len(reference) else np.nan
            row[prefix+'_reference_std'] = float(sd) if len(reference)>1 else np.nan
            for weeks, suffix in [(1, 'delta1'), (4, 'delta4')]:
                previous = scope_known[scope_known.week_ending == ref-pd.Timedelta(weeks=weeks)]
                row[prefix+'_'+suffix] = float(current[col])-float(previous.iloc[-1][col]) if len(previous) else np.nan
        z = row['inv_z']
        row['state'] = 'Unavailable' if pd.isna(z) else ('Low' if z < -1 else 'High' if z > 1 else 'Normal')
        row['low_hinge'] = max(0., -1-z) if pd.notna(z) else np.nan
        row['high_hinge'] = max(0., z-1) if pd.notna(z) else np.nan
        row['inv_interaction'] = z*row['inv_delta1']
        result.append(row)
    return pd.DataFrame(result)


def public_observations(inventory: pd.DataFrame, futures: pd.DataFrame) -> pd.DataFrame:
    """Lagged rank quotes for mechanism description only; never trading labels."""
    if inventory.empty or futures.empty:
        return pd.DataFrame()
    prices = futures.copy().sort_values('trade_date')
    prices['trade_date'] = pd.to_datetime(prices.trade_date)
    prices = prices.dropna(subset=['F2', 'F3'])
    records = []
    for _, row in inventory.iterrows():
        release = pd.Timestamp(row.release_date)
        # Stop the descriptive series when the public data stops. No stale-price extension.
        if release > prices.trade_date.max() + pd.Timedelta(days=1):
            continue
        available = prices[prices.trade_date < release]
        if available.empty:
            continue
        quote = available.iloc[-1]
        if (release-quote.trade_date).days > 7:
            continue
        if pd.isna(row.inv_z):
            continue
        records.append({'decision_date': row.release_date, 'week_ending': row.week_ending,
            'cushing_mbbl': row.cushing_mbbl, 'inv_z': row.inv_z, 'state': row.state,
            'price_date': quote.trade_date.strftime('%Y-%m-%d'),
            'F2': float(quote.F2), 'F3': float(quote.F3), 'spread': float(quote.F2-quote.F3),
            'price_identity': 'EIA daily delivery rank; actual contract identities unavailable',
            'relationship': 'inventory published after the matched quote; descriptive association only'})
    return pd.DataFrame(records)


def partition_for(day: str, config: dict) -> str:
    year = pd.Timestamp(day).year
    if year in config['test_years']:
        return 'test'
    if year in config['recent_years']:
        return 'recent'
    if year in config['validation_years']:
        return 'validation'
    return 'train' if day >= config['train_start'] else 'warmup'


def build_events(inventory: pd.DataFrame, contracts: pd.DataFrame, prices: pd.DataFrame,
                 sessions: pd.DataFrame, config: dict, near_rank: int=2,
                 execution_delay: int=0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use same contract pair for backward features and forward dollar-change label."""
    days = sorted(sessions.trade_date.astype(str).unique())
    close_times = pd.to_datetime(sessions.settlement_time, utc=True)
    close_by_day = dict(zip(sessions.trade_date.astype(str), close_times))
    panel = prices.pivot(index='trade_date', columns='contract_id', values='settlement').sort_index()
    contract_info = contracts.copy().sort_values(['last_trade_date','delivery_month'])
    inventory = inventory.sort_values('release_date').reset_index(drop=True)
    entries = [bisect_right(days, str(day))+execution_delay for day in inventory.release_date]
    complete, excluded = [], []
    for i, row in inventory.iterrows():
        event = {'event_id': str(row.report_id), 'decision_date': str(row.release_date),
                 'cutoff': str(row.knowledge_cutoff), 'week_ending': row.week_ending,
                 'cushing_mbbl': row.cushing_mbbl, 'state': row.state}
        def reject(reason, status='excluded'):
            excluded.append({**event, 'reason': reason, 'status': status})
        entry_idx = entries[i]
        if entry_idx >= len(days):
            reject('Execution session is beyond the supplied calendar', 'pending'); continue
        entry = days[entry_idx]
        event['entry_date'] = entry
        if entry > config['data_cutoff']:
            reject('Execution occurs after the fixed data cutoff', 'pending'); continue
        active = contract_info[contract_info.last_trade_date > str(row.release_date)]
        if len(active) < near_rank+1:
            reject('Insufficient identified unexpired monthly contracts'); continue
        near, far = active.iloc[near_rank-1], active.iloc[near_rank]
        event.update(near_contract=near.contract_id, far_contract=far.contract_id)
        if near.contract_id not in panel or far.contract_id not in panel:
            reject('Selected contract missing from price panel'); continue
        cutoff = pd.Timestamp(row.knowledge_cutoff)
        known = [j for j, day in enumerate(days) if close_by_day[day] <= cutoff]
        known_idx = (known[-1] if known else -1) - int(config.get('market_price_lag_sessions', 1))
        if known_idx < 20:
            reject('Insufficient 20-session known price history'); continue
        hist_days = days[known_idx-20:known_idx+1]
        hist = panel.reindex(hist_days)[[near.contract_id, far.contract_id]]
        if hist.isna().any().any():
            reject('Missing selected-contract price in feature window'); continue
        s = hist[near.contract_id]-hist[far.contract_id]
        expiry_idx = bisect_left(days, str(near.last_trade_date))
        if expiry_idx >= len(days) or days[expiry_idx] != str(near.last_trade_date):
            reject('Actual expiry missing from verified session calendar'); continue
        protected_idx = expiry_idx-int(config['expiry_buffer_sessions'])
        cap_idx = entry_idx+int(config['max_holding_sessions'])
        candidates = [(protected_idx,'expiry_buffer'), (cap_idx,'holding_cap')]
        if i+1 < len(entries):
            candidates.append((entries[i+1], 'next_release'))
        exit_idx, exit_reason = min(candidates)
        if exit_idx <= entry_idx:
            reject('No holding interval before expiry protection'); continue
        if exit_idx >= len(days) or days[exit_idx] > config['data_cutoff']:
            reject('The complete target is not observable at the data cutoff','pending'); continue
        exit_day = days[exit_idx]
        partition = partition_for(str(row.release_date),config)
        if partition == 'warmup':
            reject('Warmup only'); continue
        if partition_for(entry, config) != partition or partition_for(exit_day, config) != partition:
            reject('Target crosses a train/validation/test/recent partition boundary'); continue
        required = ['inv_z','inv_delta1','inv_delta4',
                    'low_hinge','high_hinge','inv_interaction']
        if any(pd.isna(row.get(name)) for name in required):
            reject('Incomplete publication-aware inventory features'); continue
        entry_quote = panel.reindex([entry])[[near.contract_id,far.contract_id]]
        if entry_quote.isna().any().any():
            reject('Entry prices unavailable'); continue
        # Never silently omit a losing interval because a held mark is missing.
        holding = panel.reindex(days[entry_idx:exit_idx+1])[[near.contract_id,far.contract_id]]
        if holding.isna().any().any():
            raise ValueError(f'Missing held-contract settlement in {entry} through {exit_day}: '
                             f'{near.contract_id}/{far.contract_id}; full backtest halted')
        target_s = holding[near.contract_id]-holding[far.contract_id]
        angle = 2*math.pi*(_season_day(row.week_ending)-1)/366
        event.update(exit_date=exit_day, exit_reason=exit_reason, partition=partition,
            known_price_date=days[known_idx], spread=float(s.iloc[-1]),
            delta_spread_5=float(s.iloc[-1]-s.iloc[-6]),
            vol_spread_20=float(s.diff().dropna().std(ddof=1)),
            dte=expiry_idx-(bisect_right(days,str(row.release_date))-1),
            season_sin=math.sin(angle), season_cos=math.cos(angle),
            y=float(target_s.iloc[-1]-target_s.iloc[0]), holding_sessions=exit_idx-entry_idx,
            entry_spread=float(target_s.iloc[0]), exit_spread=float(target_s.iloc[-1]))
        event.update({name:float(row[name]) for name in required})
        event.update({name:float(row.get(name,np.nan)) for name in ['national_z','national_delta1','national_delta4']})
        complete.append(event)
    return pd.DataFrame(complete), pd.DataFrame(excluded)
