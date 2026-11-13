"""Validate the bank advisor catalog and resume its exchange-traded products."""
from datetime import datetime, timezone
from hashlib import sha256
import argparse
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from acquire import MACRO
from market_inputs import BASE, SOURCE, etf_prices, weekly_macro
from resume import one_etf
from run import advisor, advisor_reference, close, js

ETFS = ('AGG','HYG','IEF','JNK','LQD','QQQ','SOXX','SPY','TLT','VOO','VT','XLK')
FROZEN = ('FSPTX','VITAX')
CODES = (*ETFS,'SHY')

def records(df):
    return json.loads(df.to_json(orient='records',date_format='iso',double_precision=15))

def input_prices(folder, expected):
    manifest = json.loads((folder/'manifest.json').read_text())
    for name, spec in manifest['responses'].items():
        if sha256((folder/name).read_bytes()).hexdigest()!=spec['sha256']:
            raise ValueError(f'Input SHA-256 mismatch {name}')
    target = pd.Timestamp(manifest['last_close'])
    macro_expected = {f'{series}_{day:%Y%m%d}.csv' for day in complete_weeks(target) for series in MACRO}
    missing = macro_expected.difference(manifest['responses'])
    if missing:
        raise PartialData(f'Missing ALFRED vintages: {sorted(missing)}')
    if expected[-1] != target:
        raise ValueError('Cutoff and calendar do not match')
    market = {}
    for symbol in CODES:
        original,kind = advisor.load_price(SOURCE/f'{symbol}.json')
        if kind != 'ETF':
            raise ValueError(f'Unexpected instrument type {symbol}')
        series,_,scale = etf_prices(folder,symbol,original,BASE)
        if not series.index[series.index>BASE].equals(expected):
            raise PartialData(f'{symbol} missing expected sessions')
        market[symbol] = series[['tr_open','tr_close']]
    weekly = pd.read_csv(SOURCE/'weekly.csv',parse_dates=['macro_vintage_date'])
    weekly,evidence = weekly_macro(folder,weekly)
    macro = advisor.grouped_macro(advisor.add_first_vintage(weekly),'base')
    signals = advisor.aligned_signals(macro,market['QQQ'].index,release_lag=1)
    if signals.loc[expected,'score'].isna().any():
        raise PartialData('Macro unavailable on required sessions')
    return market,signals,evidence

class PartialData(RuntimeError):
    pass

def date_slice(end):
    import exchange_calendars as calendars
    exchange = calendars.get_calendar('XNYS')
    dates = exchange.sessions_in_range(BASE+pd.Timedelta(days=1),end)
    if dates[-1]!=end:
        raise ValueError('Requested cutoff is not a completed US session')
    return dates

def complete_weeks(cutoff):
    import exchange_calendars as calendars
    exchange = calendars.get_calendar('XNYS')
    sessions = date_slice(cutoff)
    for period, group in pd.Series(sessions,index=sessions).groupby(sessions.to_period('W-FRI')):
        week = exchange.sessions_in_range(period.start_time.normalize(),period.end_time.normalize())
        if len(week) and group.iloc[-1] == week[-1]:
            yield group.iloc[-1]

def metric_values(nav):
    stats = advisor.metrics(nav)
    return {k:None if pd.isna(v) else js(v) for k,v in stats.items()}

def checkpoint(symbol,plan,year,old,market,signals,daily,orders):
    nav = daily.nav
    peak = max(1.,nav.max())
    mdd = float((nav/nav.cummax().clip(lower=1.)-1).min())
    old.update(buy_count=int(orders.side.eq('BUY').sum()),sell_count=int(orders.side.eq('SELL').sum()),
               direct_cost=float(orders.cost_per_initial_dollar.sum()))
    last = market[symbol].join(market['SHY'].add_prefix('shy_')).loc[nav.index[-1]]
    return js(dict(asset=symbol,plan=plan,year=year,version=1,state=old,
        risk=dict(peak_nav=peak,max_drawdown=mdd,start=nav.index[0],end=nav.index[-1]),
        signal=signals.loc[nav.index[-1]].to_dict(),market=last.to_dict()))

def archive_match(actual,frozen):
    close(np.asarray([actual['cumulative'],actual['cagr'],actual['mdd']],dtype=float),
          [frozen.cumulative,frozen.cagr,frozen.mdd],atol=2e-11)
    for k in ('peak','trough','recovered','peak_to_recovery_calendar_days','trough_to_recovery_calendar_days',
              'unresolved_drawdown_calendar_days','longest_underwater_sessions','worst_year'):
        left,right=actual[k],frozen[k]
        if pd.isna(right):
            assert left is None,k
        elif isinstance(right,str):
            assert left==right,k
        else:
            assert abs(left-right)<1e-10,k

def source_nav(data):
    return pd.Series([x[1] for x in data],index=pd.DatetimeIndex([x[0] for x in data]),name='nav')

def bootstrap(archive, bank_data, prior_pilot, inputs, destination):
    phase = pd.read_csv(archive/'results/advisor/summary.csv')
    site = json.load(gzip.open(bank_data))
    bank = pd.DataFrame(site['advisor'])
    assert set(bank.symbol)==set(ETFS)|set(FROZEN)
    frozen = bank[bank.symbol.isin(FROZEN)]
    market,signals,evidence = input_prices(inputs,date_slice(pd.Timestamp('2026-11-12')))
    summaries, daily_rows, trade_rows = [], [], []
    states = {}
    archived = phase[phase.symbol.isin(ETFS)]
    assert set(zip(archived.symbol,archived.start_year,archived.variant))==set(
        zip(bank[bank.symbol.isin(ETFS)].symbol,bank[bank.symbol.isin(ETFS)].start_year,bank[bank.symbol.isin(ETFS)].variant))
    for row in archived.itertuples(index=False):
        sym,year,plan = row.symbol,row.start_year,row.variant
        start=pd.Timestamp(row.start)
        price=market[sym].join(market['SHY'].add_prefix('shy_'))
        key=f'{sym}_{year}_{plan}'
        if (sym,year)==('QQQ',2006):
            # Consume the previously verified QQQ account/checkpoint and six added rows.
            base=pd.read_csv(prior_pilot/'source/qqq_daily.csv.gz').query('variant==@plan')
            extra=pd.read_csv(prior_pilot/'output/account_nav.csv').query('asset=="QQQ" & plan==@plan')
            history=pd.Series(base.nav.to_numpy(),index=pd.to_datetime(base.date),name='nav')
            added=pd.Series(extra.iloc[1:].nav.to_numpy(),index=pd.to_datetime(extra.iloc[1:].date),name='nav')
            cp=json.loads((prior_pilot/'output/checkpoints'/f'QQQ_{plan}_latest.json').read_text()) | {'year':year}
            assert cp['risk']['end']==str(price.index[-1].date())
            close(cp['market']['tr_close'],price.tr_close.iloc[-1])
            orders=pd.DataFrame()
            daily_rows.extend(extra.iloc[1:][['date','nav','cash','target_value','shy_value','score','state']].assign(symbol=sym,year=year,plan=plan).to_dict('records'))
        else:
            history_d,old_o,old_state=advisor.replay(sym,price.loc[:BASE],signals,start,plan,False)
            history=history_d.nav
            close(old_o.cost_per_initial_dollar.sum(),row.transaction_cost_initial_fraction)
            assert (sum(old_o.side=='BUY'),sum(old_o.side=='SELL'))==(row.buy_count,row.sell_count)
            cp0=checkpoint(sym,plan,year,old_state,market,signals,history_d,old_o)
            updated=one_etf(plan,cp0,{'QQQ':(price.iloc[:,:2],None,None),'SHY':(market['SHY'],None,None)},signals)
            info,daily,orders=updated
            cp=js({**cp0,**info})
            added=daily.set_index('date').nav
            full,full_o=advisor_reference.replay(sym,price,signals,start,plan,False)
            pd.testing.assert_series_equal(full.nav,pd.concat([history,added]),check_exact=True,check_names=True)
            if len(orders):
                pd.testing.assert_frame_equal(full_o,pd.concat([old_o,orders],ignore_index=True),check_exact=True)
            else:
                pd.testing.assert_frame_equal(full_o,old_o,check_exact=True)
            daily_rows.extend(records(daily.assign(symbol=sym,year=year,plan=plan,
                target_value=daily.nav*daily.target_weight,shy_value=daily.nav*daily.defense_weight,cash=daily.nav*daily.cash_weight)))
            trade_rows.extend(records(orders.assign(cohort_symbol=sym,year=year,plan=plan)))
        old_metrics=metric_values(history)
        archive_match(old_metrics,pd.Series(row._asdict()))
        nav=pd.concat([history,added])
        updated_stats=metric_values(nav)
        original=bank[(bank.symbol==sym)&(bank.start_year==year)&(bank.variant==plan)].iloc[0]
        close([original.cumulative,original.cagr,original.mdd],np.asarray([old_metrics[k] for k in ('cumulative','cagr','mdd')],dtype=float))
        summaries.append(dict(symbol=sym,start_year=year,variant=plan,through=str(nav.index[-1].date()),
            status='VERIFIED',archive_cumulative=original.cumulative,archive_cagr=None if pd.isna(original.cagr) else original.cagr,
            archive_mdd=original.mdd,**updated_stats,buy_count=cp['state']['buy_count'],sell_count=cp['state']['sell_count'],
            transaction_cost_initial_fraction=cp['state']['direct_cost'],
            added_buys=int(sum(orders.side=='BUY')) if len(orders) else 0,added_sells=int(sum(orders.side=='SELL')) if len(orders) else 0))
        states[key]=dict(checkpoint=cp,nav=[[str(i.date()),float(n)] for i,n in nav.items()])
    payload=dict(scope='ETF',through='2026-11-12',summary=summaries,daily=daily_rows,trades=trade_rows,
        frozen=records(frozen),macro_evidence=evidence)
    save(destination,payload,states)
    return payload

def save(destination,payload,states):
    destination.parent.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    with TemporaryDirectory(dir=destination.parent) as t:
        draft=Path(t)/'batch'
        draft.mkdir()
        raw=json.dumps(payload,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
        (draft/'results.json').write_bytes(raw)
        with gzip.open(draft/'checkpoints.json.gz','wt') as f:
            json.dump(states,f,separators=(',',':'),allow_nan=False)
        manifest=dict(batch='etf-'+sha256(raw).hexdigest()[:16],status='VERIFIED',through=payload['through'],
            calculated_at=datetime.now(timezone.utc).isoformat(),results_sha256=sha256(raw).hexdigest(),
            checkpoints_sha256=sha256((draft/'checkpoints.json.gz').read_bytes()).hexdigest(),accounts=len(states))
        (draft/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        draft.rename(destination)

def validate_prior(prior):
    previous=json.loads((prior/'manifest.json').read_text())
    if previous['status']!='VERIFIED' or sha256((prior/'results.json').read_bytes()).hexdigest()!=previous['results_sha256'] or sha256((prior/'checkpoints.json.gz').read_bytes()).hexdigest()!=previous['checkpoints_sha256']:
        raise ValueError('Prior batch checksum mismatch')
    return previous

def advance(prior,inputs,destination,cutoff):
    previous=validate_prior(prior)
    if cutoff<=pd.Timestamp(previous['through']):
        return 'NO_NEW_DATA'
    market,signals,evidence=input_prices(inputs,date_slice(cutoff))
    return advance_frames(prior,market,signals,destination,cutoff,evidence)

def advance_frames(prior,market,signals,destination,cutoff,evidence):
    states=json.load(gzip.open(prior/'checkpoints.json.gz'))
    old=json.loads((prior/'results.json').read_bytes())
    previous_summaries={(r['symbol'],r['start_year'],r['variant']):r for r in old['summary']}
    assert len(states)==len(previous_summaries)==976
    new,daily,trades=[],[],[]
    for key,item in states.items():
        cp=item['checkpoint']
        symbol,year,plan=cp['asset'],cp['year'],cp['plan']
        context={'QQQ':(market[symbol],None,None),'SHY':(market['SHY'],None,None)}
        info,added,orders=one_etf(plan,cp,context,signals)
        nav=pd.concat([source_nav(item['nav']),added.set_index('date').nav])
        assert nav.index.is_unique and nav.index.is_monotonic_increasing
        stats=metric_values(nav)
        close(np.asarray([stats[k] for k in ('cumulative','cagr','mdd')],dtype=float),np.asarray([info['performance'][k] for k in ('cumulative','cagr','mdd')],dtype=float))
        updated=js(cp|info)
        states[key]=dict(checkpoint=updated,nav=[[str(i.date()),float(n)] for i,n in nav.items()])
        new.append(previous_summaries[symbol,year,plan]|stats|dict(through=str(cutoff.date()),
            buy_count=updated['state']['buy_count'],sell_count=updated['state']['sell_count'],
            transaction_cost_initial_fraction=updated['state']['direct_cost'],added_buys=int(sum(orders.side=='BUY')),
            added_sells=int(sum(orders.side=='SELL'))))
        daily.extend(records(added.assign(symbol=symbol,year=year,plan=plan,target_value=added.nav*added.target_weight,
            shy_value=added.nav*added.defense_weight,cash=added.nav*added.cash_weight)))
        trades.extend(records(orders.assign(cohort_symbol=symbol,year=year,plan=plan)))
    save(destination,dict(scope='ETF',through=str(cutoff.date()),summary=new,daily=daily,trades=trades,
        frozen=old['frozen'],macro_evidence=evidence),states)
    return 'VERIFIED'

def main():
    p=argparse.ArgumentParser()
    p.add_argument('mode',choices=['bootstrap','advance'])
    p.add_argument('--inputs',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--archive',type=Path)
    p.add_argument('--bank',type=Path)
    p.add_argument('--pilot',type=Path)
    p.add_argument('--prior',type=Path)
    p.add_argument('--cutoff',type=pd.Timestamp)
    a=p.parse_args()
    if a.mode=='bootstrap':
        result=bootstrap(a.archive,a.bank,a.pilot,a.inputs,a.output)
        print(len(result['summary']),result['through'])
    else:
        print(advance(a.prior,a.inputs,a.output,a.cutoff))

if __name__=='__main__':
    main()
