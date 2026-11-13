"""Archive and replay point-in-time primary strategy inputs without crossing gaps."""
from datetime import datetime
import gzip
import importlib
import json
from pathlib import Path
import sys

import exchange_calendars as calendars
import numpy as np
import pandas as pd

from live_etf import weekly_history
from primary import ResearchGap, adapt_factory, extend_account
from run import js

ROOT = Path(__file__).resolve().parent
ARCHIVE_END = '2026-11-04'
EXPECTED_ACCOUNTS = {'US': 42, 'TW': 22}


def sealed_engine(market):
    folder = ROOT/'primary_engine'/market.lower()
    sys.path.insert(0,str(folder))
    try:
        engine = importlib.import_module('event_engine' if market=='US' else 'backtest')
    finally:
        sys.path.remove(str(folder))
    import equity_ab
    return engine,adapt_factory(equity_ab,market,engine)[1],json.loads((folder/'config.json').read_text())


def load_archive(inputs, market, days):
    folder = inputs/'primary-inputs'/market
    by_day = {}
    for date in days:
        path = folder/f'{date.date()}.json'
        if not path.exists():
            path = inputs/'observed-primary-inputs'/market/f'{date.date()}.json'
        if path.exists():
            data = json.loads(path.read_text())
            if (data['market']!=market or data['cutoff']!=str(date.date()) or
                    data['status'] not in ('RESEARCH_VERIFIED','POINT_IN_TIME_INPUT_VERIFIED')):
                raise ValueError(f'{market} {date.date()}: invalid archive identity')
            by_day[date] = data
    return by_day


def point_in_time(archive, market):
    day = archive['cutoff']
    missing = []
    stocks = archive['stocks']
    expected = 89 if market=='US' else 80
    if len(stocks)!=expected:
        raise ResearchGap(f'{day}: expected {expected} instruments, received {len(stocks)}')
    for symbol, stock in stocks.items():
        if stock.get('marketEvent'):
            continue
        f = stock['financial']
        valid = False
        if market=='US':
            filed = f.get('max_input_filed')
            valid = bool(filed) and pd.Timestamp(filed)<=pd.Timestamp(day)-pd.Timedelta(days=2)
        else:
            proof = f.get('filingEvidence') or {}
            close = datetime.fromisoformat(day+'T13:30:00+08:00')
            version=stock.get('financialVersion')
            if version:
                first=datetime.fromisoformat(version['firstVerifiedAt'])
                earliest=datetime.fromisoformat(version['eligibleFrom'])
                official=version.get('officialPublishedAt')
                valid=(version['period']==f.get('latest_period') and version['source']=='FinMind' and
                       earliest<=close and (earliest==first or
                       (official and earliest==datetime.fromisoformat(official) and version.get('officialSource'))))
            else:
                valid = (proof.get('period')==f.get('latest_period') and
                         proof.get('releaseKey')==f.get('fundamental_release_key') and
                         bool(proof.get('source','').startswith('https://')) and
                         bool(proof.get('publishedAt') and proof.get('verifiedThrough')) and
                         datetime.fromisoformat(proof['publishedAt'])<=close<=datetime.fromisoformat(proof['verifiedThrough']))
        if not valid or not stock['bars'] or stock['bars'][-1]['date']!=day:
            missing.append(symbol)
    return missing


def prices_and_rows(archive, day, market, macro, cfg, engine, scale_by_symbol):
    rows, quotes = [], {}
    for symbol, stock in archive['stocks'].items():
        if stock.get('marketEvent'):
            continue
        f = {k: np.nan if v is None else v for k,v in stock['financial'].items()}
        bars = stock['bars']
        last, prior = bars[-1],bars[-2]
        scale = scale_by_symbol[symbol]
        quotes[symbol] = dict(open=last['open'],adj_open=last['adjOpen']*scale,adj_close=last['adjClose']*scale,
                              adj_mark=last['adjClose']*scale,observed_price=True,opening_gap=last['open']/prior['close']-1)
        features = {k:np.nan if v is None else v for k,v in stock['features'].items()}
        row = f|features|dict(symbol=symbol,signal_date=day,industry=stock['industry'],name=stock['name'],
            macro_status='vintage_verified',macro_stress=macro['score'],macro_vintage_date=macro['date'],
            funding_good=macro['score']<=cfg['entry_max_macro_stress'],
            technical_divergence=bool(stock['evaluation']['divergence']))
        if market=='US':
            row['financial_event_date']=pd.Timestamp(f['max_input_filed'])+pd.Timedelta(days=cfg['fundamental_lag_days'])
            row['tradable']=bool(stock['evaluation']['tradable'])
        else:
            version=stock.get('financialVersion')
            available=version['eligibleFrom'] if version else f['filingEvidence']['publishedAt']
            event = pd.Timestamp(available).tz_convert('Asia/Taipei').tz_localize(None).normalize()
            row.update(financial_event_date=event,financial_age_days=(day-event).days,
                fundamental_release_key=f'{symbol}-{pd.Timestamp(f["latest_period"]).to_period("Q")}',
                tradable=bool(stock['evaluation']['tradable']))
        rows.append(row)
    frame = pd.DataFrame(rows)
    if market=='US':
        frame['fundamental_good']=engine.fundamental_good(frame,cfg)
        eligible=frame.loc[frame.fundamental_good&frame.tradable]
        rank=engine.rank
        fundamentals=.45*rank(eligible.revenue_growth)+.20*rank(eligible.growth_acceleration)+.20*rank(eligible.operating_margin)+.15*rank(eligible.cfo_margin)
        value=eligible.groupby('industry').price_sales.transform(lambda s:rank(-s))
        value=value.where(eligible.groupby('industry').price_sales.transform('count')>=3,.5)
        frame['fundamental_value_score']=100*(.75*fundamentals+.25*value)
    else:
        frame['fundamental_good']=~frame.apply(lambda row:bool(engine.financial_reason(row,None,cfg)),axis=1)
        common=frame.fundamental_good&frame.funding_good&frame.tradable
        pool=frame.loc[common]
        frame['selection_score']=.5*pool.revenue_growth.rank(pct=True)+.25*pool.operating_margin.rank(pct=True)+.25*pool.cfo_margin.rank(pct=True)
    return frame,quotes


def evaluate_market(inputs, release, output, market):
    cutoff = release['snapshot']['markets'][market]['macro']['marketAsOf']
    recent=inputs/'market-observations'/market/'latest.json'
    if recent.exists():
        observation=json.loads(recent.read_text())
        if observation['market']==market and observation['status']=='PRICES_VERIFIED':
            cutoff=max(cutoff,observation['day'])
    directory=output/'primary'/market
    target=directory/'checkpoint.json.gz'
    prior=target if target.is_file() else ROOT/'seed-primary'/f'{market}.json.gz'
    accounts=json.loads(gzip.decompress(prior.read_bytes()))
    dates={a['summary']['through'] for a in accounts.values()}
    if len(dates)!=1 or len(accounts)!=EXPECTED_ACCOUNTS[market]:
        raise ValueError('Incomplete primary checkpoint')
    anchor=dates.pop()
    last=release['attempts'].get(market,{})
    if cutoff<=anchor:
        return dict(status='NO_NEW_DATA',through=anchor,requested=cutoff,detail=last.get('detail',''))
    calendar=calendars.get_calendar('XNYS' if market=='US' else 'XTAI')
    sessions=calendar.sessions_in_range(pd.Timestamp(anchor)+pd.Timedelta(days=1),pd.Timestamp(cutoff))
    archives=load_archive(inputs,market,sessions.insert(0,pd.Timestamp(anchor)))
    missing=[str(day.date()) for day in sessions if day not in archives]
    filing={str(day.date()):codes for day,arc in archives.items() if day>pd.Timestamp(anchor)
            and (codes:=point_in_time(arc,market))}
    result=dict(status='WAITING_FOR_CONTINUITY',through=anchor,requested=cutoff,
        archived_sessions=[str(day.date()) for day in archives if day>pd.Timestamp(anchor)],
        missing_sessions=missing,filing_or_quote_gaps=filing)
    if missing or filing:
        result['detail']='需要逐日且已驗證的決策與成交資料'
        return result
    if pd.Timestamp(anchor) not in archives:
        result['detail']='缺少檢查點當日的決策列和調整價格基準'
        return result
    # A sealed price basis must be supplied by the verified primary checkpoint.
    # Never reuse a new provider adjustment scale for old units.
    basis_path=directory/'price-basis.json'
    if not basis_path.exists():
        result['detail']='缺少主策略檢查點同基準的已驗證價格及決策橋接'
        return result
    basis=json.loads(basis_path.read_text())
    if basis['date']!=anchor:
        raise ResearchGap('Primary quote basis anchor differs from account checkpoints')
    engine,kernel,cfg=sealed_engine(market)
    max_us=cutoff if market=='US' else release['snapshot']['markets']['US']['macro']['marketAsOf']
    from equity_ab import grouped_macro
    weekly=weekly_history(inputs,pd.Timestamp(max_us))[0]
    grouped=grouped_macro(weekly,'base').set_index('macro_vintage_date')
    base=archives[pd.Timestamp(anchor)]
    scales={}
    for symbol, stock in base['stocks'].items():
        if not stock.get('marketEvent'):
            close=stock['bars'][-1]['adjClose']
            scales[symbol]=basis['prices'][symbol]/close
    frames, charts = [], {}
    for day,archive in archives.items():
        available=day if market=='US' else day-pd.Timedelta(days=1)
        macro=grouped.loc[:available].iloc[-1]
        if macro.grouped_status!='vintage_verified':
            raise ResearchGap(f'{market} {day.date()}: original macro vintage unavailable')
        # Match each archived curve's base observation, including revisions
        # caused by distributions since the previous run.
        scales_today={}
        for symbol, stock in archive['stocks'].items():
            if stock.get('marketEvent'):
                continue
            anchor_quote=next((b for b in stock['bars'] if b['date']==anchor),None)
            if anchor_quote is None or symbol not in scales:
                raise ResearchGap(f'{market} {day.date()}: no price overlap for {symbol}')
            scales_today[symbol]=basis['prices'][symbol]/anchor_quote['adjClose']
        frame,quotes=prices_and_rows(archive,day,market,dict(score=macro.grouped_score,date=macro.name),cfg,engine,scales_today)
        frames.append(frame)
        for symbol, quote in quotes.items():
            charts.setdefault(symbol,{})[day]=quote
    panel=pd.concat(frames,ignore_index=True)
    charts={s:pd.DataFrame.from_dict(rows,orient='index') for s,rows in charts.items()}
    if market=='US':
        charts['QQQ']=pd.DataFrame(index=pd.DatetimeIndex(list(archives)))
        index,ranked={},{}
        for day,frame in panel.groupby('signal_date'):
            index[day]=frame.set_index('symbol').to_dict('index')
            ranked[day]=frame.loc[frame.fundamental_good&frame.funding_good&frame.tradable].sort_values(
                ['fundamental_value_score','symbol'],ascending=[False,True]).to_dict('records')
        def run(start,end,variant,state):
            return kernel(None,charts,cfg|{'start':start,'end':end},'event_no_divergence',
                          lookups=(ranked,index),extras=variant=='B',checkpoint=state)
    else:
        exchange=engine.Market(panel,charts,pd.DatetimeIndex(list(archives)),cfg)
        def run(start,end,variant,state):
            return kernel(exchange,start,end,'primary',extras=variant=='B',checkpoint=state)
    return publish_primary(run,accounts,cutoff,market,directory,charts,panel)


def publish_primary(run, accounts, cutoff, market, directory, charts, panel):
    history_path=directory/'release.json'
    history=json.loads(history_path.read_text())['curves'] if history_path.exists() else {}
    from production import save
    checkpoints, summaries, ledger = {}, [], {}
    for key, cp in accounts.items():
        extended=extend_account(run,cp,cutoff)
        checkpoints[key]={k:js(extended[k]) for k in ('summary','state')}
        summaries.append(checkpoints[key]['summary'])
        ledger[key]={k:js(v.reset_index().to_dict('records')) for k,v in extended.items()
                     if k not in ('summary','state') and v is not None}
        curve=history.setdefault(key,[])
        curve.extend([[str(day.date()),float(nav)] for day,nav in extended['daily'].nav.items()])
        if len(curve)!=len({day for day,_ in curve}):
            raise ValueError('Primary valuation dates overlap')
    directory.mkdir(parents=True,exist_ok=True)
    # Normalize all payloads before replacing any checkpoint file.
    decisions=js(panel.loc[panel.signal_date>pd.Timestamp(next(iter(accounts.values()))['summary']['through'])]
                 .to_dict('records'))
    save(directory/'ledgers'/f'{cutoff}.json',dict(accounts=ledger,decisions=decisions))
    (directory/'checkpoint.json.gz').write_bytes(gzip.compress(json.dumps(checkpoints,allow_nan=False).encode(),mtime=0))
    save(directory/'price-basis.json',dict(date=cutoff,prices={s:float(frame.loc[pd.Timestamp(cutoff),'adj_close'])
        for s,frame in charts.items() if 'adj_close' in frame and pd.Timestamp(cutoff) in frame.index}))
    latest={key:dict(positions=rows['positions'],orders=rows['orders']) for key,rows in ledger.items()}
    save(directory/'release.json',dict(schema=1,market=market,through=cutoff,summary=summaries,curves=history,latest=latest))
    return dict(status='VERIFIED',through=cutoff,requested=cutoff,cohorts=len(summaries),detail='')
