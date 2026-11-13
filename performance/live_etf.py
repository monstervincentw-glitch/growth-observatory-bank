"""Advance the verified ETF accounts using the bank research producer's saved inputs."""
from datetime import datetime, timezone
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from etf import CODES, PartialData, complete_weeks, date_slice, records
from market_inputs import SOURCE, weekly_macro
from publication import decode, encode
from resume import one_etf
from run import advisor, js

PRICE_BASIS = 'USD split/dividend-adjusted total return'


def read_state(folder):
    raw = (folder / 'state.json.gz').read_bytes()
    rows = json.loads((folder / 'release.json').read_text())
    state = json.loads(gzip.decompress(raw))
    manifest = json.loads((folder / 'manifest.json').read_text())
    view = decode(rows)
    if (sha256(raw).hexdigest() != manifest['state_sha256'] or
            view['batch'] != manifest['batch'] or view['through'] != manifest['through']):
        raise ValueError('Verified ETF state does not match the manifest')
    keys = {(s['symbol'], s['start_year'], s['variant']) for s in view['summary']}
    if {(v['asset'], v['year'], v['plan']) for v in state.values()} != keys or len(state) != len(keys):
        raise ValueError('Verified ETF state does not match the published cohorts')
    return manifest, view, state


def archive_state(folder, view, state, overlap):
    folder.mkdir(parents=True, exist_ok=True)
    raw = gzip.compress(json.dumps(state, separators=(',', ':'), allow_nan=False).encode(), mtime=0)
    (folder / 'state.json.gz').write_bytes(raw)
    (folder / 'release.json').write_text(json.dumps(encode(view), separators=(',', ':'))+'\n')
    prices = json.dumps(overlap,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    (folder / 'overlap.json').write_bytes(prices)
    (folder / 'manifest.json').write_text(json.dumps(dict(batch=view['batch'], through=view['through'],
        state_sha256=sha256(raw).hexdigest(), overlap_sha256=sha256(prices).hexdigest()), sort_keys=True)+'\n')


def weekly_history(data_root, cutoff, *, for_etf=False):
    weekly = pd.read_csv(SOURCE/'weekly.csv', parse_dates=['macro_vintage_date'])
    with TemporaryDirectory() as temporary:
        folder = Path(temporary)
        sessions = date_slice(cutoff)
        for day in complete_weeks(cutoff):
            name = str(day.date())
            path = data_root/'weekly-vintages'/f'{name}.json'
            if name == '2026-11-06' and not path.exists():
                path = SOURCE.parent/'seed-weekly'/f'{name}.json'
            if not path.exists():
                if for_etf and (sessions>day).sum()<2:
                    continue
                raise PartialData(f'ALFRED vintage absent: {name}')
            vintage = json.loads(path.read_text())
            if vintage['day'] != name or vintage['source'] != 'ALFRED':
                raise ValueError('Invalid ALFRED provenance')
            if set(vintage['series']) != {'CPIAUCSL','DFII10','FEDFUNDS','NFCI'}:
                raise PartialData(f'Incomplete ALFRED vintage: {name}')
            for series, csv in vintage['series'].items():
                (folder/f'{series}_{day:%Y%m%d}.csv').write_text(csv)
        weekly, evidence = weekly_macro(folder, weekly)
    return weekly, evidence


def signals_for(data_root, cutoff):
    weekly, evidence = weekly_history(data_root, cutoff, for_etf=True)
    macro = advisor.grouped_macro(advisor.add_first_vintage(weekly),'base')
    flags=macro[['rate_flag','conditions_flag','inflation_flag']]
    lower=flags.eq(True).sum(axis=1)
    upper=lower+flags.isna().sum(axis=1)
    # If every possible score implies the same original red/non-red decision,
    # give the engine that decision via its existing numerical threshold.
    # Save the range in evidence and never report the proxy as a published score.
    decisive=(upper<2)|(lower>=2)
    inferred=macro.grouped_score.isna()&decisive&macro.macro_vintage_date.gt('2026-11-04')
    bounds={str(macro.loc[i,'macro_vintage_date'].date()):[int(lower[i]),int(upper[i])] for i in macro.index[inferred]}
    macro.loc[inferred,'grouped_score']=lower[inferred].astype(float)
    for item in evidence:
        if item['vintage_date'] in bounds:
            item['decision_score_range']=bounds[item['vintage_date']]
    # Use the same session lag as the original ETF replay.
    old_calendar = pd.DatetimeIndex(pd.read_csv(SOURCE/'sessions.csv')['date'])
    calendar = old_calendar.union(date_slice(cutoff))
    signals = advisor.aligned_signals(macro, calendar, release_lag=1)
    return signals, evidence


def normalized_prices(feed, state, anchor, sessions, overlap):
    if feed['status'] != 'VERIFIED':
        raise PartialData(str(feed['errors']))
    if feed['source'] != 'Yahoo Finance chart' or feed['priceBasis'] != PRICE_BASIS:
        raise ValueError('Unsupported ETF quote basis')
    if set(feed['prices']) != set(CODES):
        raise PartialData('Missing ETF or SHY history')
    reference = {cp['asset']: cp for cp in state.values()}
    marks = {s: cp['market'] for s, cp in reference.items()}
    marks['SHY'] = {k.removeprefix('shy_'): v for k, v in marks['QQQ'].items() if k.startswith('shy_')}
    result, next_overlap = {}, {}
    for symbol, bars in feed['prices'].items():
        frame = pd.DataFrame(bars).set_index('date')
        frame.index = pd.DatetimeIndex(frame.index)
        needed = sessions.insert(0, anchor)
        if not frame.index.is_unique or not frame.index.is_monotonic_increasing:
            raise ValueError(f'{symbol}: duplicate or unordered sessions')
        if len(needed.difference(frame.index)):
            raise PartialData(f'{symbol}: absent sessions {list(needed.difference(frame.index))}')
        # Yahoo can move the adjustment basis on distribution dates. Bridge on
        # the saved close and independently validate the saved adjusted open.
        frame = frame.loc[:sessions[-1], ['open','close','adjOpen','adjClose']].astype(float)
        if not np.isfinite(frame).all().all() or not frame.gt(0).all().all():
            raise ValueError(f'{symbol}: invalid quote')
        np.testing.assert_allclose(frame.adjOpen/frame.adjClose, frame.open/frame.close, rtol=1e-9)
        scale = marks[symbol]['tr_close']/frame.loc[anchor,'adjClose']
        adjusted = frame[['adjOpen','adjClose']].rename(columns={'adjOpen':'tr_open','adjClose':'tr_close'})*scale
        np.testing.assert_allclose(adjusted.loc[anchor,'tr_open'], marks[symbol]['tr_open'], rtol=0, atol=1e-8)
        reference = pd.Series(overlap[symbol],dtype=float)
        reference.index = pd.DatetimeIndex(reference.index)
        np.testing.assert_allclose(adjusted.loc[reference.index,'tr_close'], reference, rtol=1e-6, atol=1e-8)
        result[symbol] = adjusted.loc[needed]
        next_overlap[symbol] = {str(d.date()):float(v) for d,v in adjusted.tr_close.tail(4).items()}
    return result, next_overlap


def update_details(old, cp, nav, stats):
    peak, peak_day = cp['risk']['peak_nav'], cp['peak_day']
    high, trough, recovered = old['peak'], old['trough'], old['recovered']
    max_drawdown, recovery_level = old['mdd'], cp['recovery_level']
    year, year_base, prior_worst_year, prior_worst = cp['year_state']
    last_nav = 1 + old['cumulative']
    underwater, longest = cp['underwater'], old['longest_underwater_sessions']
    for date, value in nav.items():
        day = str(date.date())
        if value > peak:
            peak, peak_day = value, day
        drawdown = value/peak-1
        underwater = underwater+1 if drawdown < -1e-12 else 0
        longest = max(longest, underwater)
        if drawdown < max_drawdown:
            high, trough, recovered = peak_day, day, None
            max_drawdown, recovery_level = drawdown, peak
        if recovered is None and value >= recovery_level:
            recovered = day
        if year != date.year:
            if last_nav/year_base-1 < prior_worst:
                prior_worst_year, prior_worst = year, last_nav/year_base-1
            year, year_base = date.year, last_nav
        last_nav = value
    worst_year, worst = (year, last_nav/year_base-1) if last_nav/year_base-1 < prior_worst else (prior_worst_year, prior_worst)
    last = str(nav.index[-1].date())
    days = lambda a, b: (pd.Timestamp(a)-pd.Timestamp(b)).days
    extra = dict(peak_day=peak_day,recovery_level=recovery_level,underwater=underwater,
                 year_state=[year,year_base,prior_worst_year,prior_worst])
    updated = {k:v for k,v in old.items() if k not in ('worst_12m','worst_12m_end','annual_vol','downside_vol')}
    updated.update(stats,through=last,peak=high,trough=trough,recovered=recovered,
        peak_to_recovery_calendar_days=days(recovered,high) if recovered else None,
        trough_to_recovery_calendar_days=days(recovered,trough) if recovered else None,
        unresolved_drawdown_calendar_days=0 if recovered else days(last,high),
        longest_underwater_sessions=longest,worst_year=worst_year,worst_year_return=worst)
    return updated, extra


def advance(data_root, feed, prior, destination):
    manifest, view, state = read_state(prior)
    cutoff, anchor = map(pd.Timestamp,(feed['cutoff'], manifest['through']))
    if cutoff <= anchor:
        return None
    sessions = date_slice(cutoff)
    sessions = sessions[sessions>anchor]
    raw = (prior/'overlap.json').read_bytes()
    if sha256(raw).hexdigest()!=manifest['overlap_sha256']:
        raise ValueError('ETF quote overlap checksum mismatch')
    prices, overlap = normalized_prices(feed,state,anchor,sessions,json.loads(raw))
    signals, evidence = signals_for(data_root,cutoff)
    if signals.loc[sessions,'score'].isna().any():
        raise PartialData('ETF funding signal is unknown')
    summaries, daily, trades = [], [], []
    previous = {(s['symbol'], s['start_year'], s['variant']):s for s in view['summary']}
    for key, checkpoint in state.items():
        symbol, year, plan = checkpoint['asset'], checkpoint['year'], checkpoint['plan']
        old = previous[symbol,year,plan]
        info, days, orders = one_etf(plan,checkpoint,{'QQQ':(prices[symbol],),'SHY':(prices['SHY'],)},signals)
        if not pd.DatetimeIndex(days.date).equals(sessions) or not np.isfinite(days.nav).all() or days.nav.le(0).any():
            raise ValueError(f'{key}: invalid daily ledger')
        summary, details = update_details(old,checkpoint,days.set_index('date').nav,info['performance'])
        summary.update(buy_count=info['state']['buy_count'],sell_count=info['state']['sell_count'],
            transaction_cost_initial_fraction=info['state']['direct_cost'],
            added_buys=int(orders.side.eq('BUY').sum()),added_sells=int(orders.side.eq('SELL').sum()))
        state[key] = js(checkpoint|info|details)
        summaries.append(js(summary))
        daily.extend(records(days.assign(symbol=symbol,year=year,plan=plan)))
        trades.extend(records(orders.assign(cohort_symbol=symbol,year=year,plan=plan)))
    release = dict(schema=1,scope='ETF',through=str(cutoff.date()),summary=summaries)
    batch = 'etf-'+sha256(json.dumps(release,sort_keys=True,allow_nan=False).encode()).hexdigest()[:16]
    release.update(batch=batch,calculated_at=datetime.now(timezone.utc).isoformat())
    archive_state(destination,release,state,overlap)
    (destination/'ledger.json').write_text(json.dumps(dict(batch=batch,source_batch=feed['batch'],
        price_date=feed['cutoff'],macro_evidence=evidence,daily=daily,trades=trades),allow_nan=False)+'\n')
    return release
