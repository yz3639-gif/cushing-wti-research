"""Fail-closed import contract for individually dated futures market data."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd


REQUIRED = {
 'contracts.csv': ['contract_id','delivery_month','last_trade_date','multiplier','tick_size','exchange','currency'],
 'prices.csv': ['trade_date','contract_id','settlement'],
 'sessions.csv': ['trade_date','settlement_time'],
}


def validate_market(root: Path, config: dict) -> tuple[dict, dict]:
    directory = root/'data'/'input'
    errors, warnings, frames = [], [], {}
    for filename, required in REQUIRED.items():
        path = directory/filename
        if not path.exists():
            errors.append(f'Missing market input: {filename}'); continue
        try:
            df = pd.read_csv(path, dtype={col:str for col in required if col not in
                         ['settlement','multiplier','tick_size']})
            missing = sorted(set(required)-set(df))
            if missing: errors.append(f'{filename}: missing columns {missing}')
            elif df.empty: errors.append(f'{filename}: no market observations')
            elif df[required].isna().any().any(): errors.append(f'{filename}: missing required values')
            else: frames[filename] = df
        except Exception as exc:
            errors.append(f'{filename}: {exc}')
    metadata_path = directory/'market_metadata.json'
    try:
        metadata = json.loads(metadata_path.read_text())
        for key in ['actual_contracts_verified','expiry_dates_verified','calendar_verified']:
            if metadata.get(key) is not True: errors.append(f'Market provenance not verified: {key}')
        for key in ['source','settlement_field','verification_notes','verified_by']:
            if not str(metadata.get(key,'')).strip(): errors.append(f'Missing provenance: {key}')
    except FileNotFoundError:
        metadata = {}; errors.append('Missing or invalid market_metadata.json: required file is absent')
    except Exception as exc:
        metadata = {}; errors.append(f'Missing or invalid market_metadata.json: {exc}')
    if len(frames) == 3:
        c,p,s = frames['contracts.csv'],frames['prices.csv'],frames['sessions.csv']
        if c.contract_id.duplicated().any(): errors.append('Duplicate contract identifiers')
        if c.delivery_month.duplicated().any(): errors.append('Multiple contracts for one delivery month')
        if p.duplicated(['trade_date','contract_id']).any(): errors.append('Duplicate contract/date settlements')
        if s.trade_date.duplicated().any(): errors.append('Duplicate calendar session dates')
        if c.contract_id.str.contains(r'^CL\d{1,2}(?:\s+Comdty)?$', case=False,regex=True).any():
            errors.append('Continuous CL rank tickers are not actual dated contracts')
        if not set(p.contract_id).issubset(set(c.contract_id)): errors.append('Unmapped price contract IDs')
        if not set(p.trade_date).issubset(set(s.trade_date)): errors.append('Prices outside verified calendar')
        if not c.exchange.str.upper().isin(['NYMEX','CME','CME/NYMEX']).all(): errors.append('Non-CL exchange')
        if not c.currency.str.upper().eq('USD').all(): errors.append('Expected USD prices')
        for df, columns, label in [(c,['multiplier','tick_size'],'contracts'),(p,['settlement'],'prices')]:
            for col in columns:
                df[col] = pd.to_numeric(df[col],errors='coerce')
                if not np.isfinite(df[col]).all(): errors.append(f'{label}: non-finite {col}')
        if not np.isclose(c.multiplier,1000).all(): errors.append('CL multiplier must be 1000 barrels')
        if not np.isclose(c.tick_size,.01).all(): errors.append('CL tick size must be USD 0.01/barrel')
        for df,col in [(c,'last_trade_date'),(p,'trade_date'),(s,'trade_date')]:
            parsed = pd.to_datetime(df[col],errors='coerce')
            if parsed.isna().any(): errors.append(f'Invalid date in {col}')
            else: df[col] = parsed.dt.strftime('%Y-%m-%d')
        if not c.delivery_month.str.fullmatch(r'\d{4}-\d{2}').all(): errors.append('delivery_month must be YYYY-MM')
        try:
            months = pd.to_datetime(c.delivery_month+'-01')
            expected_months = set(pd.date_range(months.min(),months.max(),freq='MS').strftime('%Y-%m'))
            missing_months = sorted(expected_months-set(c.delivery_month))
            if missing_months: errors.append(f'Missing delivery months in contract chain: {missing_months}')
            if not (pd.to_datetime(c.last_trade_date)<months).all(): errors.append('CL last trading date must precede delivery month')
            if not (c.last_trade_date < config.get('train_start','2015-01-01')).any():
                errors.append('Contract chain must begin before the first research decision to establish the true M1 rank')
            ordered_expiries=pd.to_datetime(c.sort_values('delivery_month').last_trade_date)
            if not ordered_expiries.diff().dropna().gt(pd.Timedelta(0)).all():
                errors.append('Actual expiry dates must increase strictly with delivery month')
            if any(pd.Timestamp(value).tzinfo is None for value in s.settlement_time):
                errors.append('Settlement timestamps require an explicit timezone')
            times = pd.to_datetime(s.settlement_time,utc=True,errors='raise')
            if not times.is_monotonic_increasing: errors.append('Session settlement timestamps must be ordered')
            if not (times.dt.tz_convert('America/New_York').dt.strftime('%Y-%m-%d') == s.trade_date).all():
                errors.append('Settlement timestamp local day differs from session trade date')
            joined = p.merge(c[['contract_id','last_trade_date']],on='contract_id',validate='many_to_one')
            if (joined.trade_date>joined.last_trade_date).any(): errors.append('Settlement after actual contract expiry')
            if p.duplicated(['trade_date','contract_id']).any(): errors.append('Duplicate settlements after date normalization')
            if s.trade_date.duplicated().any(): errors.append('Duplicate calendar dates after normalization')
        except Exception as exc: errors.append(f'Market calendar/contract dates: {exc}')
        if p.trade_date.min() > '2014-11-01': errors.append('Missing pre-2015 price feature warmup')
        if p.trade_date.max() < '2025-12-31': errors.append('Actual-contract panel does not cover the complete 2022-2025 test period')
        if metadata.get('same_day_settlement_available') is not True:
            warnings.append('All decision quote features use an extra one-session lag because same-day availability is unverified')
    audit={'passed':not errors, 'errors':errors, 'warnings':warnings, 'metadata':metadata,
           'files':{name:len(df) for name,df in frames.items()},
           'rule':'No generic rank series, invented contract IDs, inferred verified expiries, or synthetic prices'}
    return frames,audit
