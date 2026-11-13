"""Restore the 17-ISIN bank NAV study from frozen inputs without network access."""
from collections import defaultdict
import csv
from dataclasses import dataclass
from datetime import date
import gzip
from hashlib import sha256
import json
import math
from pathlib import Path
import re
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'engine'))
from advisor import END, FEE, METHODS, WINDOWS, add_first_vintage, aligned_signals, load_price, metrics
from equity_ab import grouped_macro

INPUT = ROOT / 'inputs'
OUTPUT = ROOT / 'results'
START = pd.Timestamp('2006-01-01')
INITIAL = 1_000_000.0
TRUST = 0.002
BUY_SETTLE = 3
PENDING = 'LU0119063898'
LIMITED = 'LU0048622798'
EVENTS = {code: pair for code, pair in WINDOWS.items() if code in ('GFC', 'COVID', '2022', '2025_tariff')}
SHORT_FEES = {'TW000T1117Y8': (14, .003)}
CLASSES = dict(zip(
    ('LU0056508442','LU0210536867','LU1046421795','TW000T1614Y4','LU0097036916',
     'LU0232524495','LU0119063898','LU1011998942','TW000T1117Y8','TW000T2213Y4',
     'LU0261961675','LU0689472784','LU0048622798','LU0261947682','LU0095030564',
     'IE00B3K7XK29','LU0300740767'),
    ('A2','A股','A股','新臺幣級別','A2','A級別','A股','A級別','新臺幣級別','新臺幣級別',
     'A股','AT','A股','A股','A2級別','E級類別','A(acc)'),
    strict=True,
))
LABELS = {
    'A': '買入持有', 'B': '紅燈全退持現金', 'C': '紅燈減碼至 50%', 'D': '紅燈轉 SHY'
}
NAV_HEADER = ['ISIN','起始年','方案','日期','帳戶淨值TWD','可用現金TWD','待交割及配息TWD','基金市值TWD','SHY市值TWD','估列信託費TWD','基金淨值','基金匯率TWD','基金單位','SHY總報酬單位','資金分數','狀態']
TRADES_HEADER = ['基金完整中文名稱','ISIN','起始年','方案','標的','類型','指令日期','成交或權利日期','現金交割或發放日','使用訊號發布日','價格或每單位分配','TWD匯率','單位數','原幣成交額','TWD成交額','申購或交易費TWD','信託費TWD','短線費TWD','成交後可用現金TWD','幣別']
PERF_HEADER = ['基金完整中文名稱','ISIN','起始年','起始日','結束日','方案','累積報酬','CAGR','最大回撤','回撤高點','回撤低點','回本日','高點至回本日數','低點至回本日數','買入次數','賣出次數','直接成本TWD','回測驗證狀態']
EVENT_HEADER = ['基金完整中文名稱','ISIN','起始年','方案','事件代碼','區間起日','區間迄日','部分區間','事件報酬','事件最大回撤','紅燈日數']
CAT_HEADER = ['基金完整中文名稱','ISIN','分類','級別與配息政策','幣別','銀行通路','成立日','銀行NAV起日','銀行NAV迄日','回測驗證狀態','正式回測起日','正式回測迄日','總代理','申購手續費率','申購淨值日','贖回淨值日及入款','銀行產品頁','銀行NAV筆數','注意事項','通路代碼','是否開放電子交易']

@dataclass
class Lot:
    date: date
    units: float
    principal: float
    original_principal: float

@dataclass
class Receipt:
    due: date
    amount: float
    currency: str
    reinvest: bool = False

def iso(value):
    return value.strftime('%Y-%m-%d') if pd.notna(value) else ''

def num(value):
    return round(float(value), 6)

def export_value(value):
    if value is None or pd.isna(value):
        return ''
    if isinstance(value, (date, pd.Timestamp)):
        return iso(value)
    if isinstance(value, (float, np.floating)):
        return round(float(value), 10)
    return value

def csv_file(name, mode='wt'):
    path = OUTPUT / name
    if name.endswith('.gz'):
        return gzip.open(path, mode, encoding='utf-8-sig', newline='', compresslevel=4)
    return path.open('w', encoding='utf-8-sig', newline='')

def write_rows(name, header, rows):
    with csv_file(name) as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows([export_value(x) for x in row] for row in rows)

def read_inputs():
    catalog = pd.read_csv(INPUT / 'fund_catalog.csv', keep_default_na=False).set_index('ISIN')
    raw = pd.read_csv(INPUT / 'observed_fund_nav.csv.gz', parse_dates=['淨值日期'])
    source = raw[raw['通路'].eq('玉山銀行')].copy()
    assert len(source) == 81680 and raw['通路'].eq('國泰世華銀行').sum() == 20743
    assert set(source['ISIN']) == set(catalog.index) and not source.duplicated(['ISIN','淨值日期']).any()
    assert source['每單位淨值'].gt(0).all()
    assert source.groupby('ISIN').size().eq(catalog['淨值筆數']).all()
    raw_dist = pd.read_csv(INPUT / 'observed_distributions.csv', keep_default_na=False)
    aa = raw_dist[(raw_dist['ISIN'] == LIMITED) & raw_dist['配息基準日'].ge('2025-01-01')]
    assert list(aa['除息日'].str[:7]) == ['2026-08','2026-02','2025-08','2025-02']
    assert aa['發放日'].ne('').all()
    dist = {date.fromisoformat(r['配息基準日']): (date.fromisoformat(r['除息日']), date.fromisoformat(r['發放日']), float(r['每單位配息']))
            for r in aa.to_dict('records')}
    weekly = pd.read_csv(INPUT / 'weekly_funding_2006_2026.csv', parse_dates=['macro_vintage_date'])
    macro = grouped_macro(add_first_vintage(weekly), 'base')
    shy, _ = load_price(INPUT / 'SHY.json')
    assert shy.index[0] <= START and shy.index[-1] == END
    rates = {}
    for key in ('DEXTAUS','DEXUSEU'):
        series = pd.read_csv(INPUT / f'{key}.csv', parse_dates=['observation_date'], na_values='.').set_index('observation_date')[key].dropna()
        assert series.index[0] <= START and (END-series.index[-1]).days <= 5 and series.gt(0).all()
        rates[key] = series
    return catalog, source, dist, macro, shy, rates

def bank_dealing(row, isin):
    line = row['贖回淨值日及入款']
    waits = re.search(r'約T\+(\d+)(?:~(\d+))?', line)
    assert row['申購淨值日'] == 'T' and waits
    return (float(row['申購手續費率'].rstrip('%'))/100,
            int(line.startswith('T+1')),
            int(waits.group(2) or waits.group(1)),
            SHORT_FEES.get(isin, (0,0.)))

def market_frame(fund, bank_days, dist, macro, shy, rates):
    events = pd.DatetimeIndex([d for record, (ex, paid, _) in dist.items() for d in (record, ex, paid)])
    days = fund.index.union(bank_days).union(shy.index).union(events).sort_values()
    days = days[(days >= START) & (days <= END)]
    fx = {}
    for name, series in rates.items():
        # Previous published observation only. The current US observation is not known at the TW order cutoff.
        idx = series.index.searchsorted(days, side='left') - 1
        assert (idx >= 0).all()
        fx[name] = series.to_numpy()[idx]
    sign = aligned_signals(macro, shy.index, release_lag=1).reindex(days, method='ffill')
    known = sign.signal_date.notna()
    assert (sign.loc[known,'signal_date'] < days[known]).all()
    return pd.DataFrame(dict(fund=fund.reindex(days), fund_mark=fund.reindex(days,method='ffill'),
        usd=fx['DEXTAUS'], eur=fx['DEXTAUS']*fx['DEXUSEU'], shy_open=shy.tr_open.reindex(days),
        shy_mark=shy.tr_close.reindex(days,method='ffill'), score=sign.score,
        signal=sign.signal_date.dt.strftime('%Y-%m-%d').fillna(''), bank=days.isin(bank_days)), index=days)

def settle_day(days, day, wait):
    position = days.searchsorted(np.datetime64(day), side='left') + wait
    return days[position].astype('datetime64[D]').astype(date) if position < len(days) else date.max

def trust_due(lot, current, sold_units):
    days = (current-lot.date).days
    if days <= 0 or sold_units <= 0:
        return 0.
    return max(100*lot.principal/lot.original_principal, lot.principal*TRUST*days/365.2425) * sold_units/lot.units

def first_deal_date(dates, requested, lag):
    pos = dates.searchsorted(np.datetime64(requested), side='left') + lag
    return dates[pos].astype('datetime64[D]').astype(date) if pos < len(dates) else date.max

def research_events(frame, start):
    for code, (first, last) in EVENTS.items():
        event = frame.loc[first:last]
        if event.empty:
            continue
        prior = frame.loc[frame.index < event.index[0], 'nav']
        initial = prior.iloc[-1] if len(prior) else INITIAL
        curve = np.r_[initial,event.nav]
        yield (code, event.index[0],event.index[-1],bool(start > pd.Timestamp(first) or END < pd.Timestamp(last)),
               event.nav.iloc[-1]/initial-1, (curve/np.maximum.accumulate(curve)-1).min(), int(event.red.sum()))

def run_account(frame, start, variant, isin, name, currency, terms, bank_dates, us_dates, fund_dates, dividends, daily_writer, trade_writer):
    cash = INITIAL
    shares = shy_units = reinvest_cash = 0.
    lots, receipts = [], []
    ex_entitlements = {}
    pending_sell = None
    last_target = None
    rebalance = True
    buy_count = sell_count = 0
    direct_cost = 0.
    previous_fund_mark = math.nan
    load, sell_lag, redemption_lag, (short_days, short_rate) = terms
    start_year = start.year
    results = []
    available_at = date.min

    def book(target, action, request, current, settled, signal, price, fx, units, gross, fee=0., trust=0., short=0.):
        nonlocal buy_count, sell_count, direct_cost
        buy_count += int(action in ('申購','買入','配息再投入'))
        sell_count += int(action in ('贖回','賣出'))
        direct_cost += fee+trust+short
        assert cash > -1e-6 and units >= 0 and gross >= 0 and fee >= 0 and trust >= 0 and short >= 0
        if settled != date.max:
            assert current <= settled
        trade_writer.writerow([name,isin,start_year,variant,target,action,request,current,
            '' if settled == date.max else settled,signal,round(price,10),round(fx,9),round(units,10),
            num(gross),num(gross*fx),num(fee),num(trust),num(short),num(cash),currency if target == '基金' else 'USD'])

    active = frame.loc[start:]
    for ts, fund_nav, mark, usd, eur, shy_open, shy_mark, score, signal, bank in active.itertuples(name=None):
        current = ts.date()
        fx = {'TWD':1.,'USD':usd,'EUR':eur}[currency]
        cash += sum(r.amount*{'TWD':1.,'USD':usd,'EUR':eur}[r.currency] for r in receipts if r.due <= current)
        reinvest_cash += sum(r.amount*{'TWD':1.,'USD':usd,'EUR':eur}[r.currency] for r in receipts if r.due <= current and r.reinvest)
        receipts = [r for r in receipts if r.due > current]
        target = 'green' if variant == 'A' else ('unknown' if math.isnan(score) else ('red' if score >= 2 else 'green'))
        if target != last_target:
            rebalance = True
            last_target = target
        goal = 1. if target == 'green' else (.5 if variant == 'C' else 0.)
        tradable = bank and not math.isnan(fund_nav)
        if target != 'unknown' and not math.isnan(shy_open) and shy_units > 0 and target == 'green':
            proceeds = shy_units*shy_open
            fee = proceeds*usd*FEE
            cycle = 1 if current >= date(2024,5,28) else (2 if current >= date(2017,9,5) else 3)
            due = settle_day(us_dates,current,cycle)
            receipts.append(Receipt(due, proceeds*(1-FEE), 'USD'))
            book('SHY','賣出',current,current,due,signal,shy_open,usd,shy_units,proceeds,fee)
            shy_units = 0.
        if target != 'unknown' and bank and shares > 0 and rebalance and pending_sell is None and goal < 1:
            mark_before_order = previous_fund_mark
            if not math.isnan(mark_before_order):
                held = shares*mark_before_order*fx
                target_value = goal*(held+cash)
                sell_units = min(shares,max(0.,(held-target_value)/(mark_before_order*fx)))
                if sell_units > 1e-10:
                    due = first_deal_date(fund_dates, current, sell_lag)
                    pending_sell = (due, sell_units, current, signal)
                    rebalance = False
        if pending_sell is not None and current == pending_sell[0]:
            _, sell_units, order_day, order_signal = pending_sell
            available_at = settle_day(bank_dates,current,1)
            due = settle_day(bank_dates,order_day,redemption_lag)
            if due <= current:
                due = settle_day(bank_dates,current,1)
            remaining = min(sell_units,shares)
            executed = remaining
            trust = short = 0.
            for lot in lots:
                part = min(lot.units,remaining)
                if part <= 0:
                    break
                trust += trust_due(lot,current,part)
                if (order_day-lot.date).days < short_days:
                    short += part*fund_nav*fx*short_rate
                lot.principal *= 1-part/lot.units
                lot.units -= part
                remaining -= part
            assert remaining < 1e-8 and not math.isnan(fund_nav)
            lots = [lot for lot in lots if lot.units > 1e-10]
            shares = sum(lot.units for lot in lots)
            gross = executed*fund_nav
            assert gross*fx > trust+short
            receipts.append(Receipt(due,gross-(trust+short)/fx,currency))
            book('基金','贖回',order_day,current,due,order_signal,fund_nav,fx,executed,gross,trust=trust,short=short)
            pending_sell = None
            reinvest_cash = 0. if goal == 0 else reinvest_cash
        if tradable and target != 'unknown' and current >= available_at and pending_sell is None and goal > 0 and shy_units == 0:
            budget = 0.
            if goal == 1 and cash > .01:
                budget = cash
            elif goal == .5 and rebalance and cash > .01:
                # Submit a 50% cash allocation before the first observed close.
                held = shares*previous_fund_mark*fx if shares else 0.
                budget = max(0.,(cash-held)*.5)
            elif goal == .5 and reinvest_cash > .01 and shares > 0:
                budget = min(cash,reinvest_cash)
            if budget > .01:
                gross = budget/(1+load)/fx
                units = gross/fund_nav
                fee = budget-gross*fx
                cash -= budget
                lots.append(Lot(current,units,gross*fx,gross*fx))
                shares += units
                book('基金','配息再投入' if reinvest_cash >= budget-1e-8 else '申購',current,current,
                     settle_day(bank_dates,current,BUY_SETTLE),signal,fund_nav,fx,units,gross,fee)
                reinvest_cash = max(0.,reinvest_cash-budget)
                rebalance = False
        if target == 'red' and variant == 'D' and current >= available_at and not math.isnan(shy_open) and not shares and cash > .01 and pending_sell is None:
            gross = cash/(1+FEE)/usd
            units = gross/shy_open
            fee = cash-gross*usd
            cash = 0.
            shy_units += units
            cycle = 1 if current >= date(2024,5,28) else (2 if current >= date(2017,9,5) else 3)
            book('SHY','買入',current,current,settle_day(us_dates,current,cycle),signal,shy_open,usd,units,gross,fee)
        if current in dividends and shares > 0:
            ex, pay, amount = dividends[current]
            entitlement = shares*amount
            ex_entitlements[ex] = Receipt(pay,entitlement,currency,True)
            book('基金','配息權利',current,ex,pay,'',amount,fx,shares,entitlement)
        if current in ex_entitlements:
            receipts.append(ex_entitlements.pop(current))
        pending = sum(r.amount*{'TWD':1.,'USD':usd,'EUR':eur}[r.currency] for r in receipts)
        provision = sum(trust_due(lot,current,lot.units) for lot in lots)
        fund_value = shares*mark*fx if shares else 0.
        shy_value = shy_units*shy_mark*usd if shy_units else 0.
        nav = cash + pending + fund_value + shy_value - provision
        assert math.isfinite(nav) and nav > 0 and cash >= -1e-7 and pending >= -1e-7
        results.append((nav,target=='red'))
        daily_writer.writerow([isin,start_year,variant,current,num(nav),num(cash),num(pending),num(fund_value),
            num(shy_value),num(provision),'' if math.isnan(mark) else round(mark,6),round(fx,8),round(shares,8),
            round(shy_units,8),'' if math.isnan(score) else int(score),target])
        if not math.isnan(mark):
            previous_fund_mark = mark
    return pd.DataFrame(results,index=active.index,columns=['nav','red']),buy_count,sell_count,direct_cost

def main():
    OUTPUT.mkdir(exist_ok=True)
    catalog, source, distributions, macro, shy, rates = read_inputs()
    bank_days = pd.DatetimeIndex(source.loc[source['ISIN'].eq('TW000T1614Y4'),'淨值日期']).sort_values()
    bank_dates = bank_days.to_numpy()
    us_dates = shy.index.to_numpy()
    performances, event_rows, fund_rows = [], [], []
    cohorts = accounts = observations = 0
    with csv_file('daily_account_nav.csv.gz') as daily_handle, csv_file('trades.csv.gz') as trades_handle:
        daily_writer, trade_writer = csv.writer(daily_handle), csv.writer(trades_handle)
        daily_writer.writerow(NAV_HEADER)
        trade_writer.writerow(TRADES_HEADER)
        for isin,row in catalog.iterrows():
            name = row['基金完整中文名稱（銀行原文）']
            currency = {'美元':'USD','歐元':'EUR','新臺幣':'TWD'}[row['計價幣別']]
            policy = row['預計配息頻率'] if row['是否配息'] == '是' else '累計／不配息'
            share = f'{CLASSES[isin]} · {policy}'
            status = '待驗證：歷史配息不完整' if isin == PENDING else ('附條件驗證：2025 年起' if isin == LIMITED else '已驗證')
            first = ''
            explanation = '銀行端 2013–2026 可核對發放日，2006–2012 紀錄不足。' if isin == PENDING else ('配息只納入 2025 年起完整可核對區間。' if isin == LIMITED else '')
            if isin != PENDING:
                assert row['是否配息'] == ('是' if isin == LIMITED else '否')
                fund = source.loc[source['ISIN'].eq(isin)].set_index('淨值日期')['每單位淨值'].sort_index().loc[:END]
                founding = pd.Timestamp(row['成立日期（銀行欄位）'])
                earliest = max(START, founding, pd.Timestamp('2025-01-01') if isin == LIMITED else START)
                starts = fund.loc[earliest:].groupby(lambda d:d.year).head(1).index
                first = iso(starts[0])
                divs = distributions if isin == LIMITED else {}
                frame = market_frame(fund,bank_days,divs,macro,shy,rates)
                assert frame.index[-1] == END and frame.fund_mark.notna().loc[starts].all()
                terms = bank_dealing(row,isin)
                for start in starts:
                    cohorts += 1
                    for variant in METHODS:
                        daily, buys, sells, cost = run_account(frame,start,variant,isin,name,currency,terms,
                           bank_dates,us_dates,fund.index.to_numpy(),divs,daily_writer,trade_writer)
                        accounts += 1
                        observations += len(daily)
                        perf = metrics(daily.nav/INITIAL)
                        performances.append([name,isin,start.year,start,END,variant,perf['cumulative'],perf['cagr'],
                            perf['mdd'],perf['peak'],perf['trough'],perf['recovered'],perf['peak_to_recovery_calendar_days'],
                            perf['trough_to_recovery_calendar_days'],buys,sells,cost,status])
                        event_rows.extend([name,isin,start.year,variant,*event] for event in research_events(daily,start))
            fund_rows.append([name,isin,row['分類'],share,currency,row['通路'],iso(pd.Timestamp(row['成立日期（銀行欄位）'])),
                row['來源淨值起日'],row['來源淨值迄日'],status,first,iso(END) if first else '',row['台灣總代理'],
                row['申購手續費率'],row['申購淨值日'],row['贖回淨值日及入款'],row['產品頁'],row['淨值筆數'],
                explanation,row['通路代碼'],row['電子交易（銀行標示）']])
            print(isin,status,flush=True)
    write_rows('fund_catalog.csv',CAT_HEADER,fund_rows)
    write_rows('performance.csv',PERF_HEADER,performances)
    write_rows('stress_events.csv',EVENT_HEADER,event_rows)
    pd.DataFrame({'日期':shy.index,'資金分數':aligned_signals(macro,shy.index,1).score.to_numpy()}).to_csv(OUTPUT/'archived_funding_daily.csv',index=False)
    manifest = {'replay_end':iso(END),'fund_count':len(catalog),'full':15,'conditional':1,'pending':1,
                'fund_year_cohorts':cohorts,'fund_year_plan_accounts':accounts,'account_nav_rows':observations,
                'event_rows':len(event_rows),'initial_twd':INITIAL,
                'inputs_sha256':{str(p.relative_to(ROOT)):sha256(p.read_bytes()).hexdigest()
                    for folder in ('inputs','engine') for p in sorted((ROOT/folder).rglob('*')) if p.is_file() and '__pycache__' not in p.parts}}
    (OUTPUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print({k:v for k,v in manifest.items() if k!='inputs_sha256'},flush=True)

if __name__ == '__main__':
    main()
