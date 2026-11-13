"""Daily-close signals, next-session execution, asymmetric Taiwan trading costs."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
from prepare import ROOT,prepare,financial_reason

POLICIES={'primary':'主策略：不要求背離、條件出場','buy_hold':'背離買入、完全不賣'}

class Market:
    def __init__(self,panel,charts,calendar,cfg):
        self.cfg=cfg;self.calendar=calendar;self.charts=charts
        self.rows={pd.Timestamp(date):g.set_index('symbol').to_dict('index') for date,g in panel.groupby('signal_date',sort=True)}
        self.eligible={}
        for date,g in panel.groupby('signal_date',sort=True):
            common=g[g.fundamental_good&g.funding_good&g.tradable].sort_values(['selection_score','symbol'],ascending=[False,True])
            self.eligible[pd.Timestamp(date)]={'primary':common.symbol.tolist(),'buy_hold':common[common.technical_divergence].symbol.tolist()}
        self.prices={s:d.to_dict('index') for s,d in charts.items() if s not in ['0050','0052']}

    def price(self,s,date):return self.prices[s][date]

def costs(notional,cfg,side,etf=False):
    commission=max(cfg['minimum_commission_twd'],notional*cfg['commission_rate'])
    slip=notional*cfg['slippage_rate']
    tax=notional*(cfg['etf_sale_tax_rate'] if etf else cfg['stock_sale_tax_rate']) if side=='SELL' else 0.
    return commission,slip,tax

def entry_notional(budget,cfg):
    gross=budget/(1+cfg['commission_rate']+cfg['slippage_rate'])
    if gross*cfg['commission_rate']<cfg['minimum_commission_twd']:gross=(budget-cfg['minimum_commission_twd'])/(1+cfg['slippage_rate'])
    return max(0.,gross)

def exit_reason(row,entry,cfg):
    reason=financial_reason(row,entry,cfg)
    if row.get('macro_status')!='vintage_verified':reason+=';macro_data_unavailable'
    elif row.get('macro_stress',99)>=cfg['exit_macro_stress']:reason+=';funding_conditions_weakened'
    return reason.strip(';')

def run(market,start,end,policy,details=True):
    cfg=market.cfg;calendar=market.calendar
    sessions=calendar[(calendar>=pd.Timestamp(start))&(calendar<=pd.Timestamp(end))]
    previous=calendar[calendar<sessions[0]][-1]
    assert previous in market.rows
    cash=cfg['initial_capital_twd'];positions={};blocked={};pending_exits={}
    actions=[];closed=[];daily=[];rejected=[];trade_id=0
    pending_entries=market.eligible[previous][policy][:cfg['maximum_positions']]
    pending_entry_signal=previous
    for date in sessions:
        day_cost=0.;traded=0.;sold_today=set()
        for s,request in list(pending_exits.items()):
            p=market.price(s,date)
            if not p['observed_price'] or p['opening_gap']<=-.095:
                if details:rejected.append({'date':date,'signal_date':request['signal_date'],'symbol':s,'action':'SELL','reason':'missing_open_or_down_gap_at_least_9.5pct'})
                continue
            pos=positions.pop(s);gross=pos['units']*p['adj_open'];commission,slip,tax=costs(gross,cfg,'SELL')
            cost=commission+slip+tax;proceeds=gross-cost;cash+=proceeds;day_cost+=cost;traded+=gross;sold_today.add(s)
            record={**pos,'exit_signal_date':request['signal_date'],'exit_date':date,'exit_reason':request['reason'],
                    'exit_market_open':p['adj_open'],'exit_gross':gross,'exit_commission':commission,'exit_slippage':slip,'exit_tax':tax,
                    'exit_cost':cost,'exit_proceeds_net':proceeds,'pnl_net':proceeds-pos['entry_cash_out'],'return_net':proceeds/pos['entry_cash_out']-1}
            closed.append(record)
            actions.append({'date':date,'signal_date':request['signal_date'],'symbol':s,'action':'SELL','trade_id':pos['trade_id'],'notional':gross,'commission':commission,'slippage':slip,'tax':tax,'cost':cost,'reason':request['reason']})
            if any(x.startswith(('revenue_','quarterly_','operating_','margin_','financial_')) for x in request['reason'].split(';')):
                blocked[s]=request['release_key']
            del pending_exits[s]
        nav_open=cash+sum(pos['units']*(market.price(s,date)['adj_open'] if market.price(s,date)['observed_price'] else market.price(s,date)['adj_mark']) for s,pos in positions.items())
        for s in pending_entries:
            if len(positions)>=cfg['maximum_positions']:break
            if s in positions or s in sold_today:continue
            row=market.rows[pending_entry_signal][s]
            if blocked.get(s)==row['fundamental_release_key']:continue
            p=market.price(s,date)
            if not p['observed_price'] or p['opening_gap']>=.095:
                if details:rejected.append({'date':date,'signal_date':pending_entry_signal,'symbol':s,'action':'BUY','reason':'missing_open_or_up_gap_at_least_9.5pct'})
                continue
            budget=min(cash,nav_open*cfg['entry_nav_fraction'])
            if budget+1e-7<nav_open*cfg['minimum_entry_nav_fraction']:continue
            gross=entry_notional(budget,cfg);commission,slip,tax=costs(gross,cfg,'BUY');cost=commission+slip
            cash_out=gross+cost
            if gross<=0 or cash_out>cash+1e-5:continue
            cash-=cash_out;day_cost+=cost;traded+=gross;trade_id+=1
            pos={'trade_id':trade_id,'symbol':s,'name':row['name'],'units':gross/p['adj_open'],'entry_signal_date':pending_entry_signal,'entry_date':date,
                 'entry_market_open':p['adj_open'],'entry_cash_out':cash_out,'entry_commission':commission,'entry_slippage':slip,'entry_cost':cost,
                 'entry_revenue_growth':row['revenue_growth'],'entry_operating_margin':row['operating_margin'],'entry_cfo_margin':row['cfo_margin'],
                 'entry_financial_available_estimate':row['financial_event_date'],'entry_macro_vintage_date':row['macro_vintage_date'],
                 'entry_drawdown_126':row['drawdown_126'],'entry_relative_63':row['relative_63'],'entry_divergence':row['technical_divergence']}
            positions[s]=pos
            actions.append({'date':date,'signal_date':pending_entry_signal,'symbol':s,'action':'BUY','trade_id':trade_id,'notional':gross,'commission':commission,'slippage':slip,'tax':0.,'cost':cost,'reason':'fundamental_funding'+('_divergence' if policy=='buy_hold' else '')})
        marks={s:pos['units']*market.price(s,date)['adj_mark'] for s,pos in positions.items()}
        assert all(np.isfinite(v) and v>0 for v in marks.values()),'Unresolved holding value'
        nav=cash+sum(marks.values())
        assert cash>=-1e-5 and len(positions)<=cfg['maximum_positions']
        daily.append({'date':date,'nav_twd':nav,'nav':nav/cfg['initial_capital_twd'],'cash_twd':cash,'positions':len(positions),'exposure':1-cash/nav,'cost_twd':day_cost,'turnover_twd':traded,'largest_weight':max(marks.values(),default=0)/nav})
        current=market.rows[date]
        if policy=='primary':
            for s,pos in positions.items():
                reason=exit_reason(current[s],pos,cfg)
                if reason and s not in pending_exits:pending_exits[s]={'signal_date':date,'reason':reason,'release_key':current[s]['fundamental_release_key']}
        slots=cfg['maximum_positions']-(len(positions)-len(pending_exits))
        pending_entries=[s for s in market.eligible[date][policy] if s not in positions and s not in pending_exits and blocked.get(s)!=current[s]['fundamental_release_key']][:max(0,slots)]
        pending_entry_signal=date
    open_rows=[]
    for s,pos in positions.items():
        mark=pos['units']*market.price(s,sessions[-1])['adj_mark']
        commission,slip,tax=costs(mark,cfg,'SELL')
        open_rows.append({**pos,'valuation_date':sessions[-1],'market_value':mark,'unrealized_pnl':mark-pos['entry_cash_out'],'unrealized_return':mark/pos['entry_cash_out']-1,
                          'ending_weight':mark/nav,'hypothetical_liquidation_cost':commission+slip+tax,'pending_exit_reason':pending_exits.get(s,{}).get('reason','')})
    daily=pd.DataFrame(daily).set_index('date');a=pd.DataFrame(actions);c=pd.DataFrame(closed);o=pd.DataFrame(open_rows)
    realized=sum(x['pnl_net'] for x in closed);unrealized=sum(x['unrealized_pnl'] for x in open_rows)
    assert np.isclose(cfg['initial_capital_twd']+realized+unrealized,daily.nav_twd.iloc[-1])
    if policy=='buy_hold':assert not closed and len(actions)<=cfg['maximum_positions'] and len(actions)==len(open_rows)
    return daily,a,c,o,pd.DataFrame(rejected)

def metrics(d,a,o,cfg):
    nav=d.nav;count=len(d)
    buys=a[a.action=='BUY'] if not a.empty else pd.DataFrame();sells=a[a.action=='SELL'] if not a.empty else pd.DataFrame()
    full=d[d.positions==cfg['maximum_positions']]
    liquidation_cost=o.hypothetical_liquidation_cost.sum() if not o.empty else 0.
    return {'total_return':float(nav.iloc[-1]-1),'cagr':float(nav.iloc[-1]**(252/count)-1) if count>=252 else np.nan,
            'max_drawdown':float((nav/nav.cummax().clip(lower=1)-1).min()),'ending_value_twd':float(d.nav_twd.iloc[-1]),
            'liquidation_return':float((d.nav_twd.iloc[-1]-liquidation_cost)/cfg['initial_capital_twd']-1),
            'sessions':count,'average_exposure':float(d.exposure.mean()),'peak_single_stock_weight':float(d.largest_weight.max()),
            'buys':len(buys),'sells':len(sells),'first_buy':str(buys.date.min().date()) if not buys.empty else None,
            'full_ten_date':str(full.index[0].date()) if len(full) else None,'open_positions':int(d.positions.iloc[-1]),'total_cost_twd':float(d.cost_twd.sum())}

def main():
    cfg=json.loads((ROOT/'config.json').read_text());out=ROOT/'results';out.mkdir(exist_ok=True)
    frozen={'created_before_calculation_utc':datetime.now(timezone.utc).isoformat(),'config':cfg,'config_sha256':hashlib.sha256((ROOT/'config.json').read_bytes()).hexdigest(),'universe_sha256':hashlib.sha256((ROOT/'universe.csv').read_bytes()).hexdigest(),'source_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in ROOT.glob('*.py')},'raw_sha256':{f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in (ROOT/'data/raw').glob('*.json')}}
    (out/'run_manifest.json').write_text(json.dumps(frozen,indent=2,ensure_ascii=False))
    panel,charts,calendar,end,cfg=prepare();market=Market(panel,charts,calendar,cfg)
    summaries=[];actions=[];closed=[];opened=[];navs=[];rejected=[];cache={};picks=[]
    for year in range(2021,2027):
        summary={'cohort_year':year,'end':str(end.date())}
        for policy in POLICIES:
            result=run(market,f'{year}-01-01',end,policy);cache[(year,1,policy)]=result
            d,a,c,o,r=result
            for frame,items in [(a,actions),(c,closed),(o,opened),(r,rejected)]:
                if not frame.empty:items.append(frame.assign(cohort_year=year,policy=policy))
            navs.append(d.reset_index().assign(cohort_year=year,policy=policy));d.to_csv(out/f'daily_{year}_{policy}.csv')
            for k,v in metrics(d,a,o,cfg).items():summary[policy+'_'+k]=v
            if not a.empty:
                for rank,row in enumerate(a[a.action=='BUY'].head(10).to_dict('records'),1):
                    item={'cohort_year':year,'policy':policy,'order':rank,'symbol':row['symbol'],'name':market.rows[pd.Timestamp(row['signal_date'])][row['symbol']]['name'],'entry_date':row['date'],'entry_signal_date':row['signal_date'],'entry_cash_out':row['notional']+row['cost']}
                    if policy=='buy_hold':
                        pos=o[o.symbol==row['symbol']].iloc[0]
                        item.update(unrealized_return=pos.unrealized_return,ending_weight=pos.ending_weight,unrealized_pnl=pos.unrealized_pnl)
                    picks.append(item)
        summary['winner']='背離持有' if summary['buy_hold_total_return']>summary['primary_total_return'] else '主策略'
        summary['lower_drawdown']='背離持有' if summary['buy_hold_max_drawdown']>summary['primary_max_drawdown'] else '主策略'
        for symbol in ['0050','0052']:
            sessions=calendar[(calendar>=pd.Timestamp(f'{year}-01-01'))&(calendar<=end)]
            benchmark=charts[symbol]
            summary[symbol+'_total_return']=float(benchmark.loc[sessions[-1],'adj_close']/benchmark.loc[sessions[0],'adj_open']/(1+cfg['commission_rate']+cfg['slippage_rate'])-1)
        summaries.append(summary);print(year,{k:round(summary[k]*100,2) for k in ['primary_total_return','buy_hold_total_return']},flush=True)
    summary=pd.DataFrame(summaries);summary.to_csv(out/'annual_summary.csv',index=False)
    def concat(frames,name):
        (pd.concat(frames,ignore_index=True) if frames else pd.DataFrame()).to_csv(out/name,index=False)
    for frames,name in [(actions,'annual_actions.csv'),(closed,'annual_closed_trades.csv'),(opened,'annual_open_positions.csv'),(navs,'annual_daily_nav.csv'),(rejected,'rejected_orders.csv')]:concat(frames,name)
    pd.DataFrame(picks).to_csv(out/'initial_ten.csv',index=False,encoding='utf-8-sig')
    # Monthly independent starts; equal 12/24/36-calendar-month horizons, matured only.
    rolling=[]
    for month in pd.date_range('2021-01-01',end,freq='MS'):
        horizons=[h for h in [12,24,36] if month+pd.DateOffset(months=h)-pd.Timedelta(days=1)<=end]
        if not horizons:continue
        for policy in POLICIES:
            key=(month.year,month.month,policy)
            if key not in cache:cache[key]=run(market,month,end,policy,details=False)
            d,a,c,o,r=cache[key]
            for h in horizons:
                target=month+pd.DateOffset(months=h)-pd.Timedelta(days=1);path=d.loc[:target];nav=path.nav
                rolling.append({'start_month':str(month.date()),'horizon_months':h,'policy':policy,'actual_start':str(path.index[0].date()),'actual_end':str(path.index[-1].date()),'total_return':float(nav.iloc[-1]-1),'max_drawdown':float((nav/nav.cummax().clip(lower=1)-1).min()),'sessions':len(path),'benchmark_0050_return':float(charts['0050'].loc[path.index[-1],'adj_close']/charts['0050'].loc[path.index[0],'adj_open']/(1+cfg['commission_rate']+cfg['slippage_rate'])-1)})
        if month.month%6==0:print('monthly cohorts through',str(month.date()),flush=True)
    rolling=pd.DataFrame(rolling);rolling.to_csv(out/'monthly_fixed_horizons.csv',index=False)
    # Delay financial availability by another 20 days; keep all numerical rules fixed.
    delayed,charts2,calendar2,end2,cfg2=prepare(cfg['lag_sensitivity_extra_days']);market2=Market(delayed,charts2,calendar2,cfg2)
    sensitivity=[]
    for year in range(2021,2027):
        for policy in POLICIES:
            d,a,c,o,r=run(market2,f'{year}-01-01',end2,policy,details=False)
            m=metrics(d,a,o,cfg2);m.update(cohort_year=year,policy=policy,extra_financial_lag_days=cfg['lag_sensitivity_extra_days']);sensitivity.append(m)
    pd.DataFrame(sensitivity).to_csv(out/'financial_lag_sensitivity.csv',index=False)
    from report import build_report
    build_report()
    print('Backtest completed',str(end.date()),flush=True)
if __name__=='__main__':main()
