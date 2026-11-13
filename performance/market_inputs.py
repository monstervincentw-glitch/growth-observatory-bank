"""Extend market frames without replacing any frozen observations."""
from hashlib import sha256
from pathlib import Path
import json

import numpy as np
import pandas as pd

from acquire import FUNDS, MACRO, chart

BASE = pd.Timestamp('2026-11-04')
CALENDAR = 'TW000T1614Y4'
SOURCE = Path(__file__).resolve().parent / 'source'

def alfred(folder, name, day, through):
    file = folder / f'{name}_{day:%Y%m%d}.csv'
    data = pd.read_csv(file, parse_dates=['observation_date'], na_values='.')
    assert list(data) == ['observation_date', f'{name}_{day:%Y%m%d}']
    values = data.set_index('observation_date').iloc[:,0].dropna().loc[:through]
    assert len(values) and values.index.is_unique and values.index.is_monotonic_increasing
    return values

def weekly_macro(folder, weekly):
    evidence = []
    additions = []
    candidates = sorted(folder.glob('NFCI_*.csv'))
    for file in candidates:
        day = pd.Timestamp(file.stem.split('_')[1])
        if day < BASE:
            continue
        row = {'macro_vintage_date': day, 'macro_status': 'vintage_verified', 'credit_source': 'NFCI'}
        inputs = {}
        for name in MACRO:
            values = alfred(folder, name, day, day-pd.Timedelta(days=1))
            obs, last = values.index[-1], float(values.iloc[-1])
            entry = {'series':name, 'vintage_date':str(day.date()), 'observation_date':str(obs.date()),
                     'value':last, 'reference_date':None, 'reference_value':None,
                     'reference_3m_date':None, 'reference_3m_value':None}
            if name == 'CPIAUCSL':
                year, quarter = obs-pd.DateOffset(months=12), obs-pd.DateOffset(months=3)
                year_value,quarter_value=values.get(year,np.nan),values.get(quarter,np.nan)
                row.update(cpi_yoy=last/year_value-1, cpi_3m_annualized=(last/quarter_value)**4-1,
                           cpi_observation=obs)
                entry.update(reference_date=str(year.date()), reference_value=float(year_value) if pd.notna(year_value) else None,
                             reference_3m_date=str(quarter.date()), reference_3m_value=float(quarter_value) if pd.notna(quarter_value) else None)
            else:
                earlier = values.loc[:obs-pd.Timedelta(days=90)]
                delta = last-float(earlier.iloc[-1])
                field = {'DFII10':'real_yield','FEDFUNDS':'fed_funds','NFCI':'financial_conditions'}[name]
                row[field], row[field+'_90d_change'] = last, delta
                entry.update(reference_date=str(earlier.index[-1].date()), reference_value=earlier.iloc[-1])
            inputs[name] = entry
        evidence.extend(inputs.values())
        frozen = weekly.loc[weekly.macro_vintage_date.eq(day)]
        if len(frozen):
            fields = ('real_yield','real_yield_90d_change','fed_funds','fed_funds_90d_change',
                      'financial_conditions','financial_conditions_90d_change','cpi_yoy','cpi_3m_annualized')
            if day == BASE:
                assert np.allclose([row[k] for k in fields], frozen.iloc[0][list(fields)].astype(float), atol=1e-12)
            continue
        additions.append(row)
    new = pd.DataFrame(additions)
    return pd.concat([weekly,new], ignore_index=True).sort_values('macro_vintage_date'), evidence

def etf_prices(folder, symbol, frozen, base):
    new = chart((folder/f'{symbol}.json').read_bytes(), symbol)
    old = frozen.loc[:base]
    overlap = old.index.intersection(new.index)
    assert base in overlap and len(overlap) >= 3
    assert np.allclose(new.loc[overlap,'close'], old.loc[overlap,'close'], rtol=1e-8, atol=1e-5)
    factor = old.loc[base,'tr_close']/new.loc[base,'adj_close']
    # Yahoo can back-adjust the fetched overlap when a new dividend enters the window.
    # Link new total returns to the frozen share basis without editing older rows.
    events = json.loads((folder/f'{symbol}.json').read_bytes())['chart']['result'][0].get('events', {})
    boundary = max((pd.Timestamp(e['date'],unit='s',tz='UTC').tz_convert('America/New_York')
                    .tz_localize(None).normalize()
                    for kind in ('dividends','splits','capitalGains') for e in events.get(kind,{}).values()
                    if pd.Timestamp(e['date'],unit='s',tz='UTC').tz_convert('America/New_York')
                    .tz_localize(None).normalize() <= base), default=overlap[0])
    comparable = overlap[overlap>=boundary]
    assert len(comparable) >= 3
    assert np.allclose(new.loc[comparable,'adj_close']*factor, old.loc[comparable,'tr_close'],
                       rtol=1e-6, atol=1e-5)
    added = new.loc[new.index > base].copy()
    added['tr_close'] = added.adj_close*factor
    added['tr_open'] = added.open*added.tr_close/added.close
    cols = ['tr_open','tr_close','close']
    return pd.concat([old[cols],added[cols]]), new, factor

def bank_series(folder, isin, frozen, base, max_day):
    raw = json.loads((folder/f'{isin}.json').read_bytes())
    assert not raw.get('ApiError')
    dates = pd.DatetimeIndex(pd.to_datetime(raw['Date'].split(','), format='%Y/%m/%d'))
    values = pd.Series([float(x) for x in raw['ChartData'].split(',')], index=dates)
    assert dates.is_unique and dates.is_monotonic_increasing and values.gt(0).all()
    assert dates[-1] <= max_day
    comparable = frozen.loc[frozen.index.intersection(dates)]
    assert base in comparable.index and np.allclose(values.loc[comparable.index], comparable, rtol=0, atol=1e-9)
    old = frozen.loc[:base]
    return pd.concat([old, values.loc[values.index>base]]), values

def read_market(folder, advisor, include_funds):
    manifest = json.loads((folder/'manifest.json').read_text())
    assert all(sha256((folder/name).read_bytes()).hexdigest() == rec['sha256']
               for name,rec in manifest['responses'].items())
    anchor, last_close, nav_end = (pd.Timestamp(manifest[k]) for k in ('anchor','last_close','nav_end'))
    assert anchor == BASE
    market = {}
    for symbol in ('QQQ','SHY'):
        frozen,_ = advisor.load_price(SOURCE/f'{symbol}.json')
        market[symbol] = etf_prices(folder, symbol, frozen, BASE)
        assert market[symbol][0].index[-1] == last_close
    assert market['QQQ'][0].index.equals(market['SHY'][0].index)
    weekly = pd.read_csv(SOURCE/'weekly.csv', parse_dates=['macro_vintage_date'])
    weekly, evidence = weekly_macro(folder, weekly)
    macro = advisor.grouped_macro(advisor.add_first_vintage(weekly), 'base')
    funds = {}
    if include_funds:
        archived = pd.read_csv(SOURCE/'fund_nav.csv.gz', parse_dates=['淨值日期'])
        for isin in FUNDS:
            frozen = archived.loc[archived.ISIN.eq(isin)].set_index('淨值日期')['每單位淨值'].sort_index()
            funds[isin] = bank_series(folder, isin, frozen, BASE, nav_end)
    return manifest, market, funds, macro, evidence

def fx_cutoff(folder, days):
    records = []
    for day in days:
        vintage = day-pd.Timedelta(days=1)
        values = alfred(folder,'DEXTAUS',vintage,vintage)
        records.append({'date':day, 'usd':values.iloc[-1], 'fx_observation_date':values.index[-1],
                        'fx_vintage_date':vintage, 'fx_age_days':(day-values.index[-1]).days})
    return pd.DataFrame(records).set_index('date')
