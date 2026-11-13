"""Validate existing research publications and convert saved data to replay inputs."""
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import math

import pandas as pd

from sheets_data import parse_envelope
from etf import CODES, PartialData, advance_frames, validate_prior
from run import advisor

ROOT = Path(__file__).resolve().parent
ARCHIVE_END = '2026-11-04'

def research_release(path):
    raw = json.loads(path.read_text())
    now = datetime.fromisoformat(raw['snapshot']['generatedAt'].replace('Z','+00:00'))
    validated = parse_envelope(raw['rows'],now=now)
    if validated['snapshot'] != raw['snapshot'] or raw['attempt']['batch'] != validated['batch']:
        raise ValueError('Research release and verified envelope disagree')
    return validated | {'attempt':raw['attempt']}

def price_history(stock):
    return {row['date']:row['close'] for row in stock['history']
            if math.isfinite(row['close']) and row['close'] > 0}

def coverage(snapshot, sessions):
    result={}
    for market, block in snapshot['markets'].items():
        by_symbol={s['symbol']:price_history(s) for s in block['stocks']}
        result[market]={
            'close':block['macro']['marketAsOf'],
            'adjusted_close_missing':{d:sorted(s for s,history in by_symbol.items() if d not in history) for d in sessions[market]},
            'filing_evidence_missing':sorted(s['symbol'] for s in block['stocks']
                if market=='TW' and not s['evaluation']['filingVerified']),
            'macro_range':block['macro']['scoreRange'],
            'daily_financial_version_in_snapshot':False,
            'daily_adjusted_open_in_snapshot':False,
        }
    return result

def etf_feed(path, research):
    """Read an archived replay export made by the existing data producer."""
    if path is None:
        raise PartialData('Existing release has no ETF replay export: ' + ', '.join(CODES))
    data=json.loads(path.read_text())
    if data['schema']!=1 or data['research_batch']!=research['batch'] or data['cutoff']!=research['snapshot']['markets']['US']['macro']['marketAsOf']:
        raise ValueError('ETF export provenance differs from the completed research batch')
    return data

def etf_frames(data, checkpoint_end):
    """Require complete saved ETF adjusted OHLC and original lagged weekly signals."""
    from etf import date_slice
    from market_inputs import SOURCE
    cutoff=pd.Timestamp(data['cutoff'])
    anchor=pd.Timestamp(checkpoint_end)
    sessions=date_slice(cutoff)
    added=sessions[sessions>anchor]
    if added.empty:
        return {},pd.DataFrame()
    expected=sessions[(sessions>=anchor)] if anchor>pd.Timestamp(ARCHIVE_END) else sessions.insert(0,anchor)
    market={}
    missing={}
    for symbol in CODES:
        rows=data['bars'].get(symbol,[])
        # The producer declares normalized adjusted dollars on the checkpoint's basis.
        if not rows:
            missing[symbol]=[str(day.date()) for day in expected]
            continue
        raw=pd.DataFrame(rows).set_index('date')
        raw.index=pd.DatetimeIndex(raw.index)
        if not raw.index.is_unique or not raw.index.is_monotonic_increasing or raw.index[-1]!=cutoff:
            raise ValueError(f'{symbol}: inconsistent ETF export dates')
        absent=expected.difference(raw.index)
        if len(absent):
            missing[symbol]=[str(day.date()) for day in absent]
            continue
        prices=raw.loc[expected,['tr_open','tr_close']].astype(float)
        if not prices.map(math.isfinite).all().all() or not prices.gt(0).all().all():
            raise ValueError(f'{symbol}: invalid adjusted prices')
        market[symbol]=prices
    if missing:
        raise PartialData('Missing saved ETF price rows: '+json.dumps(missing))
    original=pd.read_csv(SOURCE/'weekly.csv',parse_dates=['macro_vintage_date'])
    extra=pd.DataFrame(data['weekly_vintages'])
    if len(extra):
        extra['macro_vintage_date']=pd.to_datetime(extra['macro_vintage_date'])
        needed=['real_yield_90d_change','fed_funds_90d_change','cpi_yoy','cpi_3m_annualized',
                'financial_conditions','financial_conditions_90d_change']
        fields=extra[needed].astype(float)
        if (extra.macro_vintage_date.gt(cutoff).any() or extra.macro_vintage_date.le(ARCHIVE_END).any()
            or not extra.macro_vintage_date.is_unique or not extra.macro_status.eq('vintage_verified').all()
            or not extra.credit_source.eq('NFCI').all() or not fields.map(math.isfinite).all().all()):
            raise ValueError('ETF ALFRED vintage invalid')
        for row in extra.to_dict('records'):
            obs=row['observations']
            if set(obs)!={'CPIAUCSL','DFII10','FEDFUNDS','NFCI'} or any(
                pd.Timestamp(x['observation_date'])>=row['macro_vintage_date'] or
                pd.Timestamp(x['vintage_date'])!=row['macro_vintage_date'] for x in obs.values()):
                raise ValueError('ETF vintage observation timing invalid')
    from etf import complete_weeks
    weeks=set(complete_weeks(cutoff))
    if not weeks.issubset(set(extra.get('macro_vintage_date',[]))):
        raise PartialData('Original weekly ALFRED vintages missing: '+str(sorted(weeks.difference(set(extra.get('macro_vintage_date',[]))))))
    weekly=pd.concat([original,extra],ignore_index=True).sort_values('macro_vintage_date')
    macro=advisor.grouped_macro(advisor.add_first_vintage(weekly),'base')
    prices,_=advisor.load_price(SOURCE/'QQQ.json')
    calendar=prices.index.union(sessions)
    signals=advisor.aligned_signals(macro,calendar,release_lag=1)
    if signals.loc[added,'score'].isna().any():
        raise PartialData('ETF original weekly funding signal unavailable')
    return market,signals

def resume_etf(prior, release, export, destination):
    last=validate_prior(prior)['through']
    requested=release['snapshot']['markets']['US']['macro']['marketAsOf']
    if requested<=last:
        return 'NO_NEW_DATA'
    data=etf_feed(export,release)
    market,signals=etf_frames(data,last)
    return advance_frames(prior,market,signals,destination,pd.Timestamp(requested),
                          {'research_batch':release['batch'],'input_sha256':sha256(export.read_bytes()).hexdigest()})
