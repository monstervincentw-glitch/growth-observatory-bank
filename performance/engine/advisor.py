"""Advisor asset-level scenario replay from archived, vintage-dated signals."""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

from acquire_advisor import SYMBOLS
from equity_ab import grouped_macro

ASSET_TYPE = dict(QQQ='technology_growth', XLK='technology_growth', SOXX='semiconductor',
                  SPY='us_equity', VOO='us_equity', VT='global_equity',
                  FSPTX='active_mutual_fund', VITAX='index_mutual_fund',
                  AGG='investment_grade_mixed', LQD='investment_grade_credit',
                  IEF='treasury_duration', TLT='treasury_duration',
                  HYG='high_yield_credit', JNK='high_yield_credit')
END = pd.Timestamp('2026-11-04')
FEE = 0.0015
METHODS = tuple('ABCD')
WINDOWS = {'GFC':('2007-10-09','2009-03-09'), 'COVID':('2020-02-19','2020-03-23'),
           '2022':('2022-01-03','2022-12-30'), '2025_tariff':('2025-02-19','2025-04-08'),
           '2026_AI':('2026-10-07','2026-11-04')}


def load_price(path):
    raw = json.loads(path.read_text())['chart']['result'][0]
    dates = pd.to_datetime(raw['timestamp'],unit='s',utc=True).tz_convert('America/New_York').normalize().tz_localize(None)
    price = pd.DataFrame(raw['indicators']['quote'][0], index=dates)
    price['tr_close'] = raw['indicators']['adjclose'][0]['adjclose']
    price['tr_open'] = price.open * price.tr_close / price.close
    result = price[['tr_open','tr_close','close']].loc[:END]
    assert result.index.is_unique and result.index.is_monotonic_increasing
    assert result.index[-1] == END and result.gt(0).all().all()
    return result, raw['meta']['instrumentType']


def add_first_vintage(weekly):
    folder = Path(__file__).resolve().parent/'early_vintage'
    date = pd.Timestamp('2005-12-23')
    series = {}
    for key in ['CPIAUCSL','FEDFUNDS','DFII10','BAA','GS10']:
        raw = pd.read_csv(folder/f'{key}_20051223.csv',index_col=0,parse_dates=True,na_values='.')
        series[key] = raw.iloc[:,0].loc[:date-pd.Timedelta(days=1)].dropna()
    def change(s):
        return s.iloc[-1]-s.loc[:s.index[-1]-pd.Timedelta(days=90)].iloc[-1]
    cpi = series['CPIAUCSL']
    latest = cpi.index[-1]
    spread = (series['BAA']-series['GS10']).dropna()
    row = dict(macro_vintage_date=date, macro_status='vintage_verified',
               real_yield_90d_change=change(series['DFII10']),fed_funds_90d_change=change(series['FEDFUNDS']),
               cpi_yoy=cpi.iloc[-1]/cpi.loc[latest-pd.DateOffset(months=12)]-1,
               cpi_3m_annualized=(cpi.iloc[-1]/cpi.loc[latest-pd.DateOffset(months=3)])**4-1,
               credit_source='BAA_minus_GS10',credit_spread=spread.iloc[-1],credit_90d_change=change(spread),
               financial_conditions=np.nan,financial_conditions_90d_change=np.nan)
    return pd.concat([pd.DataFrame([row]),weekly],ignore_index=True)


def aligned_signals(macro, market_days, release_lag):
    score = macro.set_index('macro_vintage_date').grouped_score.reindex(market_days,method='ffill')
    dates = pd.Series(macro.macro_vintage_date.to_numpy(),index=macro.macro_vintage_date).reindex(market_days,method='ffill')
    shift = 1 + release_lag
    return pd.DataFrame({'score':score.shift(shift),'signal_date':dates.shift(shift)})


def allocate(units, cash, prices, weights, fees):
    values = units*prices
    gross = values.sum()+cash
    # Fixed point for transaction costs against the post-cost target allocation.
    investable = gross
    for _ in range(10):
        investable = gross - np.dot(abs(weights*investable-values), fees)
    new_values = weights*investable
    trades = new_values-values
    cost = abs(trades)*fees
    cash = gross-new_values.sum()-cost.sum()
    assert cash >= -1e-10
    return new_values/prices, max(0.,cash), trades, cost


def replay(symbol, prices, signals, start, variant, fund, settle=2, fund_fee=0., daily_red_rebalance=False, checkpoint=None):
    data = prices.loc[start:]
    sig = signals.reindex(data.index)
    market = data.to_numpy()
    codes = sig.score.to_numpy()
    holdings = np.zeros(2)
    cash = 1.
    prior_state = None
    actual_state = None
    stage = None
    proceed_on = 0
    offset = 0
    if checkpoint is not None:
        holdings = np.array(checkpoint['holdings'], dtype=float)
        cash, prior_state, actual_state, stage, proceed_on, offset = (
            checkpoint[key] for key in ('cash', 'prior_state', 'actual_state', 'stage', 'proceed_on', 'sessions')
        )
    daily = []
    orders = []
    fees = np.array([fund_fee if fund else FEE, FEE])
    for row,date in enumerate(data.index):
        i = row + offset
        target_open,target_close,def_open,def_close = market[row]
        weights = None
        fill = np.array([target_close,def_close]) if fund else np.array([target_open,def_open])
        score = codes[row]
        state = 'green' if variant=='A' else ('unknown' if np.isnan(score) else ('red' if score>=2 else 'green'))
        if state != prior_state:
            if state=='unknown':
                stage=None
            elif variant=='D' and fund and actual_state is not None:
                # Two assets use different dealing times. Redeem first and buy
                # only after an explicit settlement delay in cash.
                if holdings.sum()>0 and actual_state!=state:
                    weights=np.zeros(2)
                    stage=state
                    proceed_on=i+settle
                elif holdings.sum()==0:
                    stage=state
                    proceed_on=max(i,proceed_on)
            elif state=='green':
                weights=np.array([1.,0.])
            else:
                weights={'B':np.zeros(2),'C':np.array([.5,0.]),'D':np.array([0.,1.])}[variant]
        if daily_red_rebalance and variant=='C' and state=='red':
            weights=np.array([.5,0.])
        if stage is not None and i>=proceed_on and state==stage:
            weights=np.array([1.,0.]) if stage=='green' else np.array([0.,1.])
            stage=None
        if weights is not None:
            holdings,cash,trades,cost=allocate(holdings,cash,fill,weights,fees)
            for j,label in enumerate([symbol,'SHY']):
                if abs(trades[j])>1e-10:
                    orders.append((date,sig.signal_date.iat[row],label,'BUY' if trades[j]>0 else 'SELL',abs(trades[j]),cost[j],state, 'close' if fund else 'open', fill[j]))
            if weights.sum()>0 or variant!='D' or not fund:
                actual_state=state
        vals=holdings*np.array([target_close,def_close])
        nav=vals.sum()+cash
        daily.append((nav,cash/nav,vals[0]/nav,vals[1]/nav,score,state))
        prior_state=state
    d=pd.DataFrame(daily,index=data.index,columns=['nav','cash_weight','target_weight','defense_weight','score','state'])
    o=pd.DataFrame(orders,columns=['date','signal_date','symbol','side','notional_per_initial_dollar','cost_per_initial_dollar','state','fill','adjusted_price'])
    assert np.allclose(d[['cash_weight','target_weight','defense_weight']].sum(axis=1),1.)
    assert (pd.to_datetime(o.loc[o.signal_date.notna(),'signal_date'])<o.loc[o.signal_date.notna(),'date']).all()
    checkpoint = dict(holdings=holdings.tolist(), cash=cash, prior_state=prior_state,
                      actual_state=actual_state, stage=stage, proceed_on=proceed_on, sessions=offset+len(d))
    return d,o,checkpoint


def metrics(nav):
    nav = pd.concat([pd.Series([1.],index=[nav.index[0]-pd.Timedelta(days=1)]),nav])
    peak = nav.cummax()
    drawdown = nav/peak-1
    trough = drawdown.idxmin()
    high = nav.loc[:trough].idxmax()
    recovered = nav.loc[trough:][nav.loc[trough:]>=nav.loc[high]]
    recovery = recovered.index[0] if len(recovered) else pd.NaT
    underwater = drawdown.lt(-1e-12)
    run = underwater.groupby((~underwater).cumsum()).cumsum()
    returns = nav.pct_change().dropna()
    by_year = returns.add(1).groupby(returns.index.year).prod()-1
    yearago = pd.Series(nav.asof(nav.index-pd.DateOffset(years=1)).to_numpy(),index=nav.index)
    rolling = (nav/yearago-1).dropna()
    days = (nav.index[-1]-nav.index[0]).days
    return dict(cumulative=nav.iloc[-1]-1,cagr=nav.iloc[-1]**(365.2425/days)-1 if days>=365 else np.nan,
                mdd=drawdown.min(),peak=high,trough=trough,recovered=recovery,
                peak_to_recovery_calendar_days=(recovery-high).days if pd.notna(recovery) else np.nan,
                trough_to_recovery_calendar_days=(recovery-trough).days if pd.notna(recovery) else np.nan,
                unresolved_drawdown_calendar_days=(nav.index[-1]-high).days if pd.isna(recovery) else 0,
                longest_underwater_sessions=run.max(),worst_year=int(by_year.idxmin()),worst_year_return=by_year.min(),
                worst_12m=rolling.min(),worst_12m_end=rolling.idxmin() if len(rolling) else pd.NaT,
                annual_vol=returns.std()*np.sqrt(252), downside_vol=np.sqrt(np.mean(np.minimum(returns,0.)**2)*252))


def episode_detail(nav, baseline, states):
    result=[]
    red=states.eq('red')
    for _,group in red[red].groupby((~red).cumsum()):
        start,end=group.index[[0,-1]]
        i=nav.index.get_loc(start)
        prior=nav.index[i-1] if i else None
        target=baseline.loc[end]/(baseline.loc[prior] if prior is not None else 1.)-1
        strategy=nav.loc[end]/(nav.loc[prior] if prior is not None else 1.)-1
        result.append(dict(start=start,end=end,sessions=len(group),buy_hold_return=target,strategy_return=strategy,
                           avoided_loss=(max(0.,strategy-target) if target<0 else 0.),
                           missed_rebound=(max(0.,target-strategy) if target>0 else 0.)))
    return result


def window_detail(daily, baseline):
    for label,(start,end) in WINDOWS.items():
        date_range=daily.loc[start:end].index
        if len(date_range)==0:
            continue
        i=daily.index.get_loc(date_range[0])
        last=date_range[-1]
        prior=daily.index[i-1] if i else None
        nav0=daily.nav.loc[prior] if i else 1.
        b0=baseline.loc[prior] if i else 1.
        sub=daily.loc[date_range,'nav']
        path=np.r_[nav0,sub.to_numpy()]
        yield dict(window=label, first=date_range[0], last=last, partial=str(date_range[0].date())!=start or str(last.date())!=end,
                   strategy_return=sub.iloc[-1]/nav0-1,baseline_return=baseline.loc[last]/b0-1,
                   window_mdd=min(path/np.maximum.accumulate(path)-1),red_sessions=daily.loc[date_range,'state'].eq('red').sum())


def classification(ret, risk, base):
    dr=ret-base['cumulative']
    dm=risk-base['mdd']
    if dm>.005 and dr>=0:
        return 'risk_and_return_improved'
    if dr>.005 and dm<=.005:
        return 'return_only'
    if dm>.005 and dr<0:
        return 'risk_reduced_return_sacrificed'
    return 'no_clear_advantage'


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('input',type=Path)
    ap.add_argument('weekly',type=Path)
    ap.add_argument('output',type=Path)
    ap.add_argument('--proxy',choices=['base','strict','low','high'],default='base')
    ap.add_argument('--release-lag',type=int,choices=[0,1],default=1)
    ap.add_argument('--fund-settlement',type=int,choices=[1,2,3],default=2)
    ap.add_argument('--fund-fee-bps',type=float,default=0.)
    ap.add_argument('--daily-red-rebalance',action='store_true')
    ap.add_argument('--summary-only',action='store_true')
    args=ap.parse_args()
    out=args.output
    out.mkdir(parents=True,exist_ok=True)
    weekly=pd.read_csv(args.weekly,parse_dates=['macro_vintage_date'])
    market_days=load_price(args.input/'QQQ.json')[0].index
    macro=grouped_macro(add_first_vintage(weekly),args.proxy)
    signal=aligned_signals(macro,market_days,args.release_lag)
    defense=load_price(args.input/'SHY.json')[0]
    scenario_start=signal.loc['2011-05-25':].score.first_valid_index() if args.proxy=='strict' else pd.Timestamp('2006-01-01')
    summary=[]; daily_rows=[]; order_rows=[]; events=[]; windows=[]
    example={}
    for symbol in ASSET_TYPE:
        chart,kind=load_price(args.input/f'{symbol}.json')
        df=chart[['tr_open','tr_close']].join(defense[['tr_open','tr_close']].add_prefix('def_'),how='inner')
        market=df.loc[scenario_start:]
        assert market.index.equals(market_days[market_days>=market.index[0]]), symbol
        starts=market.groupby(market.index.year).head(1).index
        for start in starts:
            baseline=None
            base=None
            for variant in METHODS:
                d,o,_=replay(symbol,df,signal,start,variant,kind=='MUTUALFUND',args.fund_settlement,
                           args.fund_fee_bps/10000,args.daily_red_rebalance)
                stamp=dict(symbol=symbol,asset_type=ASSET_TYPE[symbol],variant=variant,start_year=start.year,start=start)
                if variant=='A':
                    baseline=d.nav
                    base=metrics(d.nav)
                    stats=base
                else:
                    stats=metrics(d.nav)
                event_rows=episode_detail(d.nav,baseline,d.state)
                row=dict(**stamp,**stats,sessions=len(d),buy_count=o.side.eq('BUY').sum(),sell_count=o.side.eq('SELL').sum(),
                         transaction_cost_initial_fraction=o.cost_per_initial_dollar.sum(),mean_cash=d.cash_weight.mean(),
                         mean_defense=d.defense_weight.mean(),mean_target=d.target_weight.mean(),
                         unknown_sessions=d.state.eq('unknown').sum(),
                         avoided_loss_episode_pp_sum=sum(x['avoided_loss'] for x in event_rows),
                         missed_rebound_episode_pp_sum=sum(x['missed_rebound'] for x in event_rows),
                         classification='buy_hold' if variant=='A' else classification(stats['cumulative'],stats['mdd'],base))
                summary.append(row)
                if args.summary_only:
                    continue
                daily_rows.append(d.rename_axis('date').reset_index().assign(**stamp))
                order_rows.append(o.assign(**stamp))
                events.extend(dict(stamp, episode_start=event['start'], **{k:v for k,v in event.items() if k!='start'}) for event in event_rows)
                windows.extend(dict(**stamp,**window) for window in window_detail(d,baseline))
                if start==starts[0] and symbol in {'SPY','QQQ','AGG','IEF'}:
                    example[symbol,variant]=d.nav
    pd.DataFrame(summary).to_csv(out/'summary.csv',index=False)
    if not args.summary_only:
        pd.concat(daily_rows,ignore_index=True).to_csv(out/'daily.csv.gz',index=False,compression='gzip',float_format='%.12g')
        pd.concat(order_rows,ignore_index=True).to_csv(out/'orders.csv',index=False)
        pd.DataFrame(events).to_csv(out/'red_episodes.csv',index=False)
        pd.DataFrame(windows).to_csv(out/'stress_windows.csv',index=False)
        fx=pd.read_csv(args.input/'DEXTAUS.csv',parse_dates=['observation_date'],index_col='observation_date',na_values='.').DEXTAUS.dropna()
        cases=[]; paths={}
        for variant in METHODS:
            nav=sum(example[s,variant]*w for s,w in dict(SPY=.4,QQQ=.2,AGG=.3,IEF=.1).items())
            fx_aligned=fx.reindex(nav.index,method='ffill')
            nav_twd=nav*fx_aligned/fx_aligned.iloc[0]
            stats=metrics(nav_twd)
            peak_value=nav_twd.loc[stats['peak']] if stats['peak'] in nav_twd else 1.
            cases.append(dict(variant=variant,start=nav.index[0],**stats,capital_twd=10_000_000,
                              max_drawdown_amount_twd=-stats['mdd']*peak_value*10_000_000,
                              equivalent_loss_per_10m_peak_twd=-stats['mdd']*10_000_000,
                              peak_value_twd=peak_value*10_000_000,ending_twd=nav_twd.iloc[-1]*10_000_000))
            paths[f'{variant}_nav_twd']=nav_twd
            paths[f'{variant}_nav_usd']=nav
        pd.DataFrame(cases).to_csv(out/'twd_10m_example.csv',index=False)
        pd.DataFrame(paths).rename_axis('date').to_csv(out/'twd_10m_daily.csv')
    print(f'{len(summary)} cohort/asset/variant records',flush=True)


if __name__=='__main__':
    main()
