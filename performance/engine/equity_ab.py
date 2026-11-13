"""Replay the frozen US/TW engines with audited grouped funding and optional extras."""
import argparse
import inspect
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError(f'Unexpected archived engine: {old}')
    return source.replace(old, new, 1)


def grouped_macro(weekly, proxy):
    real = weekly.real_yield_90d_change
    fed = weekly.fed_funds_90d_change
    cpi = weekly.cpi_yoy
    cpi3 = weekly.cpi_3m_annualized
    nfci = weekly.financial_conditions
    nfci90 = weekly.financial_conditions_90d_change
    credit = weekly.credit_source.eq('BAA_minus_GS10')
    credit_level = weekly.credit_spread
    credit_change = weekly.credit_90d_change
    floor, jump = {'base': (2., .5), 'low': (1.5, .5), 'high': (2.5, .75), 'strict': (np.inf,np.inf)}[proxy]
    inflation_known = cpi.notna() & (cpi.le(.04) | cpi3.notna())
    available = weekly.macro_status.eq('vintage_verified') & real.notna() & fed.notna() & inflation_known
    available &= np.where(credit, credit_level.notna() & credit_change.notna() & (proxy != 'strict'), nfci.notna() & nfci90.notna())
    rate_flag = real.gt(.5) | fed.gt(.25)
    credit_flag = pd.Series(np.where(credit, credit_level.gt(floor) | credit_change.gt(jump), nfci.gt(0) | nfci90.gt(.25)), index=weekly.index)
    cpi_flag = cpi.gt(.04) & cpi3.gt(cpi)
    score = (rate_flag.astype(int) + credit_flag.astype(int) + cpi_flag.astype(int)).where(available)
    return weekly.assign(rate_flag=rate_flag.where(real.notna() & fed.notna()),
                         conditions_flag=credit_flag.where(np.where(credit, available, nfci.notna() & nfci90.notna())),
                         inflation_flag=cpi_flag.where(inflation_known),
                         inflation_3m_input_missing=cpi3.isna(),
                         grouped_score=score, grouped_status=np.where(available,'vintage_verified','unknown'),
                         proxy_mode=proxy)


def eligible_extras(positions, rows, ranked, blocked, score_key, cfg, partial=False):
    core = [symbol for symbol, pos in positions.items() if pos['position_type']=='core']
    if len(core)!=cfg['maximum_positions']:
        return (), np.nan
    scores = [rows[s].get(score_key, np.nan) for s in core]
    finite = [s for s in scores if np.isfinite(s)]
    if not finite or (not partial and len(finite)!=len(core)):
        return (), np.nan
    threshold = max(finite)
    candidates = tuple(s for s in ranked if s not in positions and rows[s][score_key]>threshold
                       and blocked.get(s)!=rows[s]['fundamental_release_key'])
    return candidates, threshold


def build_us(eng):
    code = inspect.getsource(eng.run_strategy)
    swaps = [
        ('cost_bps=None):','cost_bps=None,lookups=None,extras=False,partial=False):'),
        ('    groups={pd.Timestamp(d):g for d,g in panel.groupby("signal_date",sort=True)}\n    index={d:g.set_index("symbol").to_dict("index") for d,g in groups.items()}', '    groups,index=lookups'),
        ('            for row in pending["entries"]:', '''            signal_rows=index[pending["signal_date"]]
            core_orders=pending["entries"]
            ranked=(r["symbol"] for r in groups[pending["signal_date"]])
            extra_orders=()
            threshold=np.nan
            for sequence in range(2):
                if sequence==0:
                    orders=core_orders
                else:
                    if not extras or cash+1e-8<nav_open*cfg["entry_nav_fraction"]:
                        break
                    extra_orders,threshold=eligible_extras(positions,signal_rows,ranked,blocked,"fundamental_value_score",cfg,partial)
                    orders=(signal_rows[s] | {"symbol":s} for s in extra_orders)
                for row in orders:'''),
        ('                budget=min(cash,nav_open*cfg["entry_nav_fraction"])','                budget=min(cash,nav_open*cfg["entry_nav_fraction"])'),
        ('"symbol":symbol,"units":units,"entry_index":i,','"symbol":symbol,"units":units,"entry_index":i,"position_type":"extra" if sequence else "core",'),
        ('"reason":"fundamentals_and_funding_and_divergence" if strategy!="event_no_divergence" else "fundamentals_and_funding"', '"reason":"excess_cash_high_score" if sequence else "fundamentals_and_funding","position_type":"extra" if sequence else "core","score":row["fundamental_value_score"],"core_max_score":threshold if sequence else np.nan'),
        ('"positions":len(positions),"cost_usd"', '"positions":len(positions),"core_positions":sum(p["position_type"]=="core" for p in positions.values()),"cost_usd"'),
        ('entries=entry_candidates(groups[date],positions,exits,blocked,cfg,strategy)', 'entries=entry_candidates(groups[date],positions,exits,blocked,cfg,strategy)'),
    ]
    for old,new in swaps:
        code=replace_once(code,old,new)
    start=code.index('                symbol=row["symbol"]')
    end=code.index('        quotes=',start)
    code=code[:start]+''.join('    '+line if line.strip() else line for line in code[start:end].splitlines(keepends=True))+code[end:]
    code=replace_once(code,'                    budget=min(cash,nav_open*cfg["entry_nav_fraction"])','''                    target=nav_open*cfg["entry_nav_fraction"]
                    if sequence and cash+1e-8<target:
                        break
                    budget=min(cash,target)''')

    def select(rows,positions,planned_exits,blocked,cfg,strategy):
        core_count=sum(p['position_type']=='core' and s not in planned_exits for s,p in positions.items())
        valid=[r for r in rows if r['symbol'] not in positions and blocked.get(r['symbol'])!=r['fundamental_release_key']]
        return valid[:max(0,cfg['maximum_positions']-core_count)]
    scope=dict(vars(eng), entry_candidates=select,eligible_extras=eligible_extras)
    exec(compile(code,eng.__file__,'exec'),scope)
    return scope['run_strategy']


def build_tw(eng):
    code=inspect.getsource(eng.run)
    for old,new in [
        ('details=True):','details=True,extras=False,partial=False):'),
        ('        for s in pending_entries:', '''        current_signal=market.rows[pending_entry_signal]
        threshold=np.nan
        for sequence in range(2):
            if sequence==0:
                orders=pending_entries
            else:
                if not extras or cash+1e-7<nav_open*cfg['entry_nav_fraction']:
                    break
                ranked=[s for s in market.eligible[pending_entry_signal][policy] if s not in sold_today]
                orders,threshold=eligible_extras(positions,current_signal,ranked,blocked,'selection_score',cfg,partial)
            for s in orders:'''),
        ("            if len(positions)>=cfg['maximum_positions']:break","            if not sequence and sum(p['position_type']=='core' for p in positions.values())>=cfg['maximum_positions']:break"),
        ("'name':row['name'],'units'","'name':row['name'],'position_type':'extra' if sequence else 'core','units'"),
        ("'reason':'fundamental_funding'+('_divergence' if policy=='buy_hold' else '')","'reason':'excess_cash_high_score' if sequence else 'fundamental_funding','position_type':'extra' if sequence else 'core','score':row['selection_score'],'core_max_score':threshold if sequence else np.nan"),
        ("'positions':len(positions),'exposure'","'positions':len(positions),'core_positions':sum(p['position_type']=='core' for p in positions.values()),'exposure'"),
        ("slots=cfg['maximum_positions']-(len(positions)-len(pending_exits))","slots=cfg['maximum_positions']-sum(p['position_type']=='core' and s not in pending_exits for s,p in positions.items())"),
    ]:
        code=replace_once(code,old,new)
    start=code.index('            if not sequence')
    end=code.index('        marks=',start)
    code=code[:start]+''.join('    '+line if line.strip() else line for line in code[start:end].splitlines(keepends=True))+code[end:]
    code=replace_once(code,"                budget=min(cash,nav_open*cfg['entry_nav_fraction'])","""                target=nav_open*cfg['entry_nav_fraction']
                if sequence and cash+1e-7<target:
                    break
                budget=min(cash,target)""")
    scope=dict(vars(eng),eligible_extras=eligible_extras)
    exec(compile(code,eng.__file__,'exec'),scope)
    return scope['run']


def persist(out, year, version, daily, trades, orders, positions, rejects, capital):
    folder=out/version
    folder.mkdir(parents=True,exist_ok=True)
    sells=orders[orders.action.eq('SELL')]
    buys=orders[orders.action.eq('BUY')]
    extras=buys[buys.reason.eq('excess_cash_high_score')]
    cash_col='cash_usd' if 'cash_usd' in daily else 'cash_twd'
    assert daily[cash_col].ge(-1e-5).all()
    assert daily.core_positions.le(10).all()
    assert daily.positions.le(10).all() if version=='A' else True
    assert not orders.duplicated(['date','symbol','action']).any()
    assert (pd.to_datetime(orders.signal_date)<pd.to_datetime(orders.date)).all()
    assert extras.score.gt(extras.core_max_score).all()
    assert extras.groupby('date').core_max_score.nunique().le(1).all()
    assert np.isclose(daily.nav.iloc[-1]*capital,capital+trades.pnl_net.sum()+positions.unrealized_pnl.sum())
    assert int(daily.positions.iloc[-1])==len(positions)
    for df,label in [(daily,'daily'),(trades,'closed'),(orders,'orders'),(positions,'positions'),(rejects,'rejects')]:
        if df is not None:
            df.to_csv(folder/f'{year}_{label}.csv',index=label=='daily')
    nav=daily.nav
    return dict(start_year=year,variant=version,first_session=daily.index[0].date(),end=daily.index[-1].date(),sessions=len(nav),
                cumulative=nav.iloc[-1]-1,cagr=nav.iloc[-1]**(252/len(nav))-1 if len(nav)>=252 else np.nan,
                max_drawdown=(nav/nav.cummax().clip(lower=1)-1).min(),buys=len(buys),sells=len(sells),
                extras=len(extras),max_positions=daily.positions.max(),end_positions=len(positions),
                cash_at_end=daily[cash_col].iloc[-1],mean_exposure=daily.exposure.mean(),cost=orders.cost.sum())


def replay_us(work, out, macro, years, variants):
    pit=work/'pit'
    sys.path.insert(0,str(pit))
    from replay_2006 import BASE, FIELDS, eng, load_chart, RAW
    cfg=json.loads((BASE/'config.json').read_text())
    panel=pd.read_pickle(pit/'compact_verified.pkl')[FIELDS]
    score=macro.set_index('macro_vintage_date')
    panel['macro_stress']=panel.macro_vintage_date.map(score.grouped_score)
    panel['macro_status']=panel.macro_vintage_date.map(score.grouped_status).fillna('unknown')
    panel['funding_good']=panel.macro_status.eq('vintage_verified')&panel.macro_stress.le(1)
    charts={s:load_chart(RAW/f'{s}_chart.json') for s in ['QQQ',*sorted(panel.symbol.unique())]}
    index={}
    ranked={}
    for date, rows in panel.groupby('signal_date',sort=True):
        index[date]=rows.set_index('symbol').to_dict('index')
        ranked[date]=rows.loc[rows.fundamental_good&rows.funding_good&rows.tradable].sort_values(
            ['fundamental_value_score','symbol'],ascending=[False,True]).to_dict('records')
    run=build_us(eng)
    summaries=[]
    for year in years:
        for version in variants:
            d,t,a,o,_=run(None,charts,cfg|{'start':f'{year}-01-01'},'event_no_divergence',
                         lookups=(ranked,index),extras=version!='A',partial=version=='B_partial_scores')
            row=persist(out,year,version,d,t,a,o,None,cfg['initial_capital'])
            summaries.append(row)
            print(row,flush=True)
    return summaries


def replay_tw(work, out, macro, years, variants):
    sys.path.insert(0,str(work/'growth_backtest_2016_2026/tw'))
    import backtest as eng
    from prepare import prepare
    panel,charts,calendar,end,cfg=prepare()
    score=macro.set_index('macro_vintage_date')
    panel['macro_stress']=panel.macro_vintage_date.map(score.grouped_score)
    panel['macro_status']=panel.macro_vintage_date.map(score.grouped_status).fillna('unknown')
    panel['funding_good']=panel.macro_status.eq('vintage_verified')&panel.macro_stress.le(1)
    panel['selection_score']=np.nan
    common=panel.fundamental_good&panel.funding_good&panel.tradable
    for key,weight in [('revenue_growth',.5),('operating_margin',.25),('cfo_margin',.25)]:
        ranks=panel.loc[common].groupby('signal_date')[key].rank(pct=True)
        panel.loc[common,'selection_score']=panel.loc[common,'selection_score'].fillna(0)+ranks*weight
    market=eng.Market(panel,charts,calendar,cfg)
    run=build_tw(eng)
    summaries=[]
    for year in years:
        for version in variants:
            d,a,t,o,r=run(market,f'{year}-01-01',end,'primary',extras=version!='A',partial=version=='B_partial_scores')
            row=persist(out,year,version,d,t,a,o,r,cfg['initial_capital_twd'])
            summaries.append(row)
            print(row,flush=True)
    return summaries


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('market',choices=['US','TW'])
    parser.add_argument('work',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--proxy',choices=['base','low','high','strict'],default='base')
    parser.add_argument('--first',type=int)
    parser.add_argument('--last',type=int,default=2026)
    parser.add_argument('--variants',nargs='+',default=['A','B','B_partial_scores'])
    args=parser.parse_args()
    start=args.first or (2006 if args.market=='US' and args.proxy!='strict' else (2012 if args.market=='US' else 2016))
    out=args.output
    out.mkdir(parents=True,exist_ok=True)
    weekly=pd.read_csv(args.work/'pit/weekly_funding_2006_2026.csv',parse_dates=['macro_vintage_date'])
    macro=grouped_macro(weekly,args.proxy)
    macro.to_csv(out/'macro_weekly.csv',index=False)
    summaries={'US':replay_us,'TW':replay_tw}[args.market](args.work.resolve(),out,macro,range(start,args.last+1),args.variants)
    pd.DataFrame(summaries).to_csv(out/'summary.csv',index=False)


if __name__=='__main__':
    main()
