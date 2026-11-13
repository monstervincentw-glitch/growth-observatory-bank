"""Reproduce the selected 2006 cohort, then append auditable pilot valuations."""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from market_inputs import BASE, CALENDAR, SOURCE, fx_cutoff, read_market

ROOT = Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'engine'))
import advisor
import advisor_reference
import fund_reference
import fund_replay as fund

START = pd.Timestamp('2006-01-03')
IDS = ('LU0056508442','LU0210536867')
NAMES = dict(zip(IDS,('貝萊德世界科技基金 A2 美元','摩根基金－美國科技基金 A 股美元累計'),strict=True))
PHASE = ROOT/'output'
PRICE_URLS = {
    'QQQ':'https://finance.yahoo.com/quote/QQQ/history/',
    'SHY':'https://finance.yahoo.com/quote/SHY/history/',
    'LU0056508442':'https://wealth.esunbank.com/zh-tw/fund/fund-info?fundCode=AG26',
    'LU0210536867':'https://wealth.esunbank.com/zh-tw/fund/fund-info?fundCode=CCG8',
    CALENDAR:'https://wealth.esunbank.com/zh-tw/fund/fund-info?fundCode=3303',
}

class Ledger:
    def __init__(self):
        self.rows = []
    def writerow(self, row):
        self.rows.append(row)
    def frame(self, columns):
        return pd.DataFrame(self.rows,columns=columns)

def fixture(name, **kwargs):
    return pd.read_csv(SOURCE/f'{name}.csv.gz', **kwargs)

def close(a,b,atol=1e-11,rtol=1e-11):
    np.testing.assert_allclose(a,b,atol=atol,rtol=rtol,equal_nan=True)

def js(value):
    if is_dataclass(value):
        return js(asdict(value))
    if isinstance(value,dict):
        return {str(k):js(v) for k,v in value.items()}
    if isinstance(value,(tuple,list)):
        return [js(v) for v in value]
    if isinstance(value,(pd.Timestamp,date,datetime)):
        return str(value.date() if isinstance(value,pd.Timestamp) else value)
    if isinstance(value,np.generic):
        return js(value.item())
    if isinstance(value,float) and not np.isfinite(value):
        return None
    return value

def read_fund_state(state):
    state = deepcopy(state)
    def d(x):
        return date.fromisoformat(x) if x is not None else None
    state['lots'] = [fund.Lot(d(x['date']),x['units'],x['principal'],x['original_principal']) for x in state['lots']]
    def receipt(x):
        return fund.Receipt(d(x['due']),x['amount'],x['currency'],x['reinvest'],d(x['origin']),x['wait'],x['bank'])
    state['receipts'] = [receipt(x) for x in state['receipts']]
    state['ex_entitlements'] = {d(k):receipt(v) for k,v in state['ex_entitlements'].items()}
    if state['pending_sell'] is not None:
        due,units,origin,signal = state['pending_sell']
        state['pending_sell'] = (d(due),units,d(origin),signal)
    for key in ('available_at','available_origin'):
        state[key] = d(state[key])
    if state['previous_fund_mark'] is None:
        state['previous_fund_mark'] = float('nan')
    return state

def risk_state(nav):
    return {'peak_nav':float(max(1.,nav.max())), 'max_drawdown':float(advisor.metrics(nav)['mdd']),
            'start':str(nav.index[0].date()), 'end':str(nav.index[-1].date())}

def advance_risk(check, added):
    peak = check['peak_nav']
    mdd = check['max_drawdown']
    for v in added:
        peak = max(peak,v)
        mdd = min(mdd,v/peak-1.)
    days = (added.index[-1]-pd.Timestamp(check['start'])).days+1
    return dict(cumulative=added.iloc[-1]-1, cagr=added.iloc[-1]**(365.2425/days)-1, mdd=mdd, peak_nav=peak)

def checkpoints(output, symbol, plan, stage, state, nav, **evidence):
    data = js(dict(asset=symbol, plan=plan, version=1, state=state, risk=risk_state(nav), **evidence))
    file = output/'checkpoints'/f'{symbol}_{plan}_{stage}.json'
    file.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return json.loads(file.read_text())

def compare_metrics(stats, sealed, keys):
    close([stats[k] for k in ('cumulative','cagr','mdd')],[sealed[k] for k in keys],atol=8e-11)

def result(asset, plan, historical, added, initial, holdings, base_holdings, counts, cost, quality):
    base = advisor.metrics(historical)
    update = advance_risk(risk_state(historical),added)
    new = advisor.metrics(pd.concat([historical,added]))
    close([update[k] for k in ('cumulative','cagr','mdd')],[new[k] for k in ('cumulative','cagr','mdd')])
    result = dict(asset=asset, plan=plan, currency='USD' if asset=='QQQ' else 'TWD',
                  start=str(historical.index[0].date()), archive_end=str(BASE.date()), new_end=str(added.index[-1].date()),
                  archive_account_nav=historical.iloc[-1]*initial, new_account_nav=added.iloc[-1]*initial,
                  added_sessions=len(added), added_buys=counts[0], added_sells=counts[1], added_cost=cost,
                  data_quality=quality, name=NAMES.get(asset,asset))
    for k in ('cumulative','cagr','mdd'):
        result['archive_'+k],result['new_'+k] = base[k],update[k]
    for prefix,snapshot in (('archive',base_holdings),('new',holdings)):
        for key,value in snapshot.items():
            result[f'{prefix}_{key}'] = value
    result.update({k:js(new[k]) for k in ('peak','trough','recovered','worst_year','worst_year_return')})
    return result

def compare_frames(left, right, categorical=()):
    assert list(left) == list(right) and len(left) == len(right)
    for k in left:
        if k not in categorical and pd.api.types.is_numeric_dtype(left[k]) and pd.api.types.is_numeric_dtype(right[k]):
            close(left[k],right[k],atol=2e-6,rtol=2e-10)
        else:
            assert left[k].fillna('').astype(str).tolist() == right[k].fillna('').astype(str).tolist(),k

def etf_position(row, state):
    return dict(target_units=state['holdings'][0], shy_units=state['holdings'][1],
                target_value=row.target_weight*row.nav, shy_value=row.defense_weight*row.nav,
                cash=state['cash'], pending=0., trust_provision=0., held='QQQ' if row.target_weight>.99999 else
                ('SHY' if row.defense_weight>.99999 else ('現金' if row.cash_weight>.99999 else '混合部位')))

def run_etf(market, signals, output):
    target, defense = (market[s][0][['tr_open','tr_close']] for s in ('QQQ','SHY'))
    price = target.join(defense.add_prefix('shy_'))
    assert price.index[-1] > BASE
    baseline = fixture('qqq_daily',parse_dates=['date'])
    orders = fixture('qqq_orders',parse_dates=['date','signal_date'])
    stats = fixture('qqq_summary')
    summaries, days, trades = [], [], []
    for variant in advisor.METHODS:
        hist, hist_o, state = advisor.replay('QQQ',price.loc[:BASE],signals,START,variant,False)
        sealed = baseline.query('variant==@variant').set_index('date')[hist.columns]
        assert hist.index.equals(sealed.index)
        close(hist.iloc[:,:5].to_numpy(float),sealed.iloc[:,:5].to_numpy(float),atol=6e-11)
        assert hist.state.equals(sealed.state)
        sealed_o = orders.query('variant==@variant').reset_index(drop=True)[hist_o.columns]
        compare_frames(hist_o.assign(symbol='QQQ'),sealed_o)
        sealed_s = stats.query('variant==@variant').iloc[0]
        compare_metrics(advisor.metrics(hist.nav),sealed_s,('cumulative','cagr','mdd'))
        assert (int(sealed_s.buy_count),int(sealed_s.sell_count)) == (hist_o.side.eq('BUY').sum(),hist_o.side.eq('SELL').sum())
        old_fee = hist_o.cost_per_initial_dollar.sum()
        close(old_fee,sealed_s.transaction_cost_initial_fraction)
        base_pos = etf_position(hist.iloc[-1],state)
        state.update(buy_count=int(sealed_s.buy_count),sell_count=int(sealed_s.sell_count),direct_cost=old_fee)
        cp = checkpoints(output,'QQQ',variant,'archive',state,hist.nav,
                         market=price.loc[BASE].to_dict(), signal=signals.loc[BASE].to_dict())
        new, new_o, new_state = advisor.replay('QQQ',price,signals,BASE+pd.Timedelta(days=1),variant,False,checkpoint=cp['state'])
        full, full_o = advisor_reference.replay('QQQ',price,signals,START,variant,False)
        pd.testing.assert_frame_equal(full,pd.concat([hist,new]),check_exact=True)
        pd.testing.assert_frame_equal(full_o,(pd.concat([hist_o,new_o],ignore_index=True) if len(new_o) else hist_o).astype(full_o.dtypes),check_exact=True)
        counts = (int(new_o.side.eq('BUY').sum()),int(new_o.side.eq('SELL').sum()))
        fee = float(new_o.cost_per_initial_dollar.sum())
        new_state.update(buy_count=state['buy_count']+counts[0],sell_count=state['sell_count']+counts[1],direct_cost=old_fee+fee)
        checkpoints(output,'QQQ',variant,'latest',new_state,full.nav,
                    market=price.iloc[-1].to_dict(),signal=signals.loc[full.index[-1]].to_dict())
        pos = etf_position(new.iloc[-1],new_state)
        summaries.append(result('QQQ',variant,hist.nav,new.nav,1.,pos,base_pos,counts,fee,'已核對 Yahoo 調整價與 ALFRED'))
        subset = full.loc[BASE:].copy()
        subset['target_value'] = subset.nav*subset.target_weight
        subset['shy_value'] = subset.nav*subset.defense_weight
        subset['cash'] = subset.nav*subset.cash_weight
        subset['signal_date'] = signals.loc[subset.index].signal_date
        days.extend(subset.rename_axis('date').reset_index().assign(asset='QQQ',plan=variant,currency='USD').to_dict('records'))
        trades.extend(new_o.assign(asset='QQQ',plan=variant).to_dict('records'))
    return summaries,days,trades

@contextmanager
def fund_end(end):
    previous = fund.END
    fund.END = end
    try:
        yield
    finally:
        fund.END = previous

def archive_rates():
    return {name:pd.read_csv(SOURCE/f'{name}.csv',index_col='observation_date',parse_dates=True,na_values='.')[name].dropna()
            for name in ('DEXTAUS','DEXUSEU')}

def fund_position(row):
    return dict(target_units=row['基金單位'], shy_units=row['SHY總報酬單位'], target_value=row['基金市值TWD'],
                shy_value=row['SHY市值TWD'], cash=row['可用現金TWD'], pending=row['待交割及配息TWD'],
                trust_provision=row['估列信託費TWD'], held='基金' if row['基金單位'] else ('SHY' if row['SHY總報酬單位'] else '現金及應收款'))

def fund_inputs(isin, funds, shy, macro, folder):
    fund_nav = funds[isin][0]
    calendar = funds[CALENDAR][0].index
    horizon = min(fund_nav.index[-1], calendar[-1], shy.index[-1])
    assert horizon > BASE
    old_rates = archive_rates()
    with fund_end(BASE):
        hist = fund.market_frame(fund_nav.loc[:BASE],calendar[calendar<=BASE],{},macro,shy.loc[:BASE],old_rates)
    with fund_end(horizon):
        extended = fund.market_frame(fund_nav.loc[:horizon],calendar[calendar<=horizon],{},macro,shy.loc[:horizon],old_rates)
    extension = extended.index[extended.index>BASE]
    missing = extension.difference(fund_nav.index)
    # A missing valuation on an observed bank session stops before the gap.
    missing_bank = missing.intersection(calendar)
    if len(missing_bank):
        extension = extension[extension < missing_bank[0]]
    assert len(extension) and extension[0] > BASE
    frame = pd.concat([hist,extended.loc[extension]])
    fx = fx_cutoff(folder,extension)
    frame.loc[extension,'usd'] = fx.usd
    frame.loc[extension,'eur'] = np.nan  # Both selected funds and SHY are USD.
    missing_flags = dict(first_missing_bank_nav=missing_bank[0] if len(missing_bank) else None,
                         latest_fund_nav=fund_nav.index[-1],latest_bank_day=calendar[-1])
    return frame, fx, fund_nav, calendar, missing_flags

def fund_run(frame, isin, row, variant, calendars, checkpoint=None, reference=False):
    nav_ledger, trade_ledger = Ledger(), Ledger()
    bank_days, us_days, fund_days = [a.to_numpy() for a in calendars]
    args = (frame,START,variant,isin,row['基金完整中文名稱（銀行原文）'],'USD',fund.bank_dealing(row,isin),
            bank_days,us_days,fund_days,{},nav_ledger,trade_ledger)
    if reference:
        values = (*fund_reference.run_account(*args),None)
    else:
        values = fund.run_account(*args,checkpoint=checkpoint)
    daily = nav_ledger.frame(fund.NAV_HEADER)
    trades = trade_ledger.frame(fund.TRADES_HEADER)
    return (*values,daily,trades)

def run_funds(market,funds,macro,folder,output):
    shy = market['SHY'][0]
    catalog = pd.read_csv(SOURCE/'fund_catalog.csv',keep_default_na=False).set_index('ISIN')
    archived = fixture('fund_daily_account_nav')
    archive_trades = fixture('fund_trades')
    performance = fixture('fund_performance')
    summaries,days,trades,metadata = [],[],[],{}
    fx_result = None
    for isin in IDS:
        row = catalog.loc[isin]
        frame,fx,nav,calendar,flags = fund_inputs(isin,funds,shy,macro,folder)
        if fx_result is None or len(fx)>len(fx_result):
            fx_result = fx
        metadata[isin] = flags
        calendar_all = (calendar,shy.index,nav.index)
        archive_calendars = [d[d<=BASE] for d in calendar_all]
        # Independent replay uses the observed calendars across the extended period.
        for variant in advisor.METHODS:
            hist,b0,s0,c0,state,hd,ho = fund_run(frame.loc[:BASE],isin,row,variant,archive_calendars)
            old_d = archived.query('ISIN==@isin & 方案==@variant').reset_index(drop=True)
            old_o = archive_trades.query('ISIN==@isin & 方案==@variant').reset_index(drop=True)
            compare_frames(hd,old_d)
            compare_frames(ho,old_o)
            p0 = performance.query('ISIN==@isin & 方案==@variant').iloc[0]
            compare_metrics(advisor.metrics(hist.nav/fund.INITIAL),p0,('累積報酬','CAGR','最大回撤'))
            assert (b0,s0) == (p0['買入次數'],p0['賣出次數'])
            close(c0,p0['直接成本TWD'])
            cp = checkpoints(output,isin,variant,'archive',state,hist.nav/fund.INITIAL,
                             market=frame.loc[BASE].to_dict())
            new,b1,s1,c1,st,d,o = fund_run(frame.loc[frame.index>BASE],isin,row,variant,calendar_all,read_fund_state(cp['state']))
            full,b,s,c,_,fd,fo = fund_run(frame,isin,row,variant,calendar_all,reference=True)
            pd.testing.assert_frame_equal(full,pd.concat([hist,new]),check_exact=True)
            assert (b,s,c) == (b1,s1,c1)
            assert fd.to_dict('records') == pd.concat([hd,d],ignore_index=True).to_dict('records')
            assert fo.to_dict('records') == (pd.concat([ho,o],ignore_index=True) if len(o) else ho).to_dict('records')
            checkpoints(output,isin,variant,'latest',st,full.nav/fund.INITIAL,market=frame.iloc[-1].to_dict())
            quality = f"玉山 NAV 已核對｜FRED 匯率觀測 {fx.fx_observation_date.iloc[-1].date()}（版本 {fx.fx_vintage_date.iloc[-1].date()}）"
            if fx.fx_age_days.iloc[-1] > 3:
                quality += '｜匯率發布落後'
            if flags['first_missing_bank_nav'] is not None:
                quality += '｜基金 NAV 缺日，已停止於缺口前'
            summaries.append(result(isin,variant,hist.nav/fund.INITIAL,new.nav/fund.INITIAL,fund.INITIAL,
                                    fund_position(d.iloc[-1]),fund_position(hd.iloc[-1]),(b1-b0,s1-s0),c1-c0,quality))
            columns = dict(zip(fund.NAV_HEADER,('asset','cohort','plan','date','nav','cash','pending','target_value',
                        'shy_value','trust_provision','fund_mark','usd','target_units','shy_units','score','state'),strict=True))
            detail = pd.concat([hd.iloc[[-1]],d],ignore_index=True).rename(columns=columns)
            detail['signal_date'] = frame.loc[pd.DatetimeIndex(detail.date),'signal'].to_numpy()
            detail['has_observed_nav'] = pd.DatetimeIndex(detail.date).isin(nav.index)
            days.extend(detail.assign(currency='TWD').to_dict('records'))
            trades.extend(o.to_dict('records'))
    return summaries,days,trades,metadata,fx_result

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--input',type=Path,default=SOURCE/'live_20261113')
    p.add_argument('--output',type=Path,default=PHASE)
    p.add_argument('--qqq-only',action='store_true')
    a = p.parse_args()
    output = a.output
    (output/'checkpoints').mkdir(parents=True,exist_ok=True)
    manifest,market,funds,macro,evidence = read_market(a.input,advisor,not a.qqq_only)
    qqq = market['QQQ'][0]
    signals = advisor.aligned_signals(macro,qqq.index,release_lag=1)
    assert signals.loc[signals.index>BASE,'score'].notna().all()
    summaries,days,etf_trades = run_etf(market,signals,output)
    fund_trades, fund_meta, fx = [],{},None
    if not a.qqq_only:
        s,d,fund_trades,fund_meta,fx = run_funds(market,funds,macro,a.input,output)
        summaries.extend(s)
        days.extend(d)
    source_rows = []
    for asset,(f,raw,link_factor) in market.items():
        for day,r in raw.loc[BASE:].iterrows():
            source_rows.append(dict(asset=asset,date=day,source=PRICE_URLS[asset],currency='USD',
                                    open=r.open,close=r.close,adjusted_close=r.adj_close,
                                    adjusted_close_frozen_basis=f.loc[day,'tr_close'],link_factor=link_factor))
    for asset,(_,raw) in funds.items():
        for day,val in raw.loc[BASE:].items():
            source_rows.append(dict(asset=asset,date=day,source=PRICE_URLS[asset],
                                    currency='TWD' if asset==CALENDAR else 'USD',close=val))
    signal_evidence = signals.loc[BASE:].rename_axis('date').reset_index()
    signal_evidence['earliest_observed_fx'] = None
    if fx is not None:
        signal_evidence = signal_evidence.drop(columns='earliest_observed_fx').merge(fx.reset_index(),on='date',how='outer').sort_values('date')
    evidence_columns = ['series','vintage_date','observation_date','value','reference_date','reference_value',
                        'reference_3m_date','reference_3m_value']
    datasets = {'summary':pd.DataFrame(summaries),'account_nav':pd.DataFrame(days),'market_prices':pd.DataFrame(source_rows),
                'signals_fx':signal_evidence,'macro_evidence':pd.DataFrame(evidence,columns=evidence_columns),
                'etf_trades':pd.DataFrame(etf_trades,columns=['asset','plan','date','signal_date','symbol','side',
                    'notional_per_initial_dollar','cost_per_initial_dollar','state','fill','adjusted_price']),
                'fund_trades':pd.DataFrame(fund_trades,columns=fund.TRADES_HEADER)}
    batch = 'pilot-'+sha256((a.input/'manifest.json').read_bytes()).hexdigest()[:12]
    metadata = js(dict(batch=batch,updated_at=datetime.now(timezone.utc).isoformat(),status='TEST_VERIFIED',
                       market_end=manifest['last_close'],fund_sources=fund_meta,scope='2006 cohort only',
                       accounts=len(summaries),data_sha256={}))
    for key,data in datasets.items():
        path = output/f'{key}.csv'
        data.to_csv(path,index=False)
        metadata['data_sha256'][key] = sha256(path.read_bytes()).hexdigest()
    (output/'batch.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n')
    print(datasets['summary'][['asset','plan','new_end','archive_cumulative','new_cumulative','new_cagr','new_mdd','added_buys','added_sells']].to_string(index=False))

if __name__ == '__main__':
    main()
