"""Taiwan exploratory signals; estimated, explicitly non-PIT financial releases."""
from pathlib import Path
import hashlib,json
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parent
RAW=ROOT/'data/raw'

def load_chart(symbol):
    if symbol=='0052':
        return load_sector_total_return()
    x=json.loads((RAW/f'{symbol}_chart.json').read_text())['chart']['result'][0]
    d=pd.DataFrame(x['indicators']['quote'][0])
    d.index=pd.to_datetime(x['timestamp'],unit='s',utc=True).tz_convert('Asia/Taipei').normalize().tz_localize(None)
    d.index.name='date';d['adj_close']=x['indicators']['adjclose'][0]['adjclose']
    factor=d.adj_close/d.close
    d['adj_open']=d.open*factor
    return d[~d.index.duplicated()].sort_index()

def market_calendar(end=None):
    data=json.loads((RAW/'market_calendar.json').read_text())['data']
    dates=pd.DatetimeIndex(pd.to_datetime([r['date'] for r in data])).sort_values().drop_duplicates()
    dates.name='date'
    return dates[dates<=pd.Timestamp(end)] if end is not None else dates

def load_sector_total_return():
    # Yahoo's 0052 history contains a spurious 85.7% fall dated 2025-11-17.
    # Rebuild using unadjusted source prices, cash distributions and the officially
    # documented 7-for-1 split on the actual resumption date, 2025-11-26.
    data=json.loads((RAW/'0052_finmind_price.json').read_text())['data']
    d=pd.DataFrame(data);d.index=pd.to_datetime(d.date);d=d.sort_index()
    d=d.rename(columns={'max':'high','min':'low','Trading_Volume':'volume'})
    d=d[d.close.gt(0)&d.open.gt(0)].copy();d.index.name='date'
    distributions=json.loads((RAW/'0052_finmind_dividends.json').read_text())['data']
    cash=pd.Series(0.,index=d.index)
    for row in distributions:
        day=pd.Timestamp(row['date'])
        if day in cash.index:cash.loc[day]+=float(row['stock_and_cache_dividend'])
    actions=json.loads((RAW/'corporate_actions.json').read_text())
    ratio=pd.Series(1.,index=d.index)
    for action in actions:
        if action['symbol']=='0052':
            day=pd.Timestamp(action['effective_date'])
            assert day in d.index
            ratio.loc[day]*=action['new_units_per_old_unit']
    gross=(d.close*ratio+cash)/d.close.shift(1);gross.iloc[0]=1.
    wealth=gross.cumprod();d['adj_close']=wealth/wealth.iloc[-1]*d.close.iloc[-1]
    d['adj_open']=d.open*d.adj_close/d.close
    assert d.adj_close.pct_change(fill_method=None).dropna().abs().max()<.2,'Unresolved sector corporate action'
    # Earlier Yahoo TR ratios bridge into, rather than replace, the audited FinMind
    # series. This keeps its official 2025 split correction and every later return.
    payload=json.loads((RAW/'0052_chart.json').read_text())['chart']['result'][0]
    early=pd.DataFrame(payload['indicators']['quote'][0])
    early.index=pd.to_datetime(payload['timestamp'],unit='s',utc=True).tz_convert('Asia/Taipei').normalize().tz_localize(None)
    early['adj_close']=payload['indicators']['adjclose'][0]['adjclose']
    anchor=d.index[0]
    if early.index.min()<anchor:
        assert anchor in early.index,'Missing sector TR bridge anchor'
        early['adj_close']*=d.loc[anchor,'adj_close']/early.loc[anchor,'adj_close']
        early['adj_open']=early.open*early.adj_close/early.close
        d=pd.concat([early.loc[early.index<anchor],d]).sort_index()
        assert d.index.is_unique and d.adj_close.pct_change(fill_method=None).dropna().abs().max()<.2
    return d

def financial_events(symbol,name,cfg,extra_days=0):
    i=pd.DataFrame(json.loads((RAW/f'{symbol}_income.json').read_text())['data'])
    c=pd.DataFrame(json.loads((RAW/f'{symbol}_cashflow.json').read_text())['data'])
    if i.empty or c.empty:raise ValueError('No financial statements')
    def pivot(d):
        d=d.copy();d['period']=pd.to_datetime(d.date).dt.to_period('Q')
        return d.pivot_table(index='period',columns='type',values='value',aggfunc='last')
    income=pivot(i);cash=pivot(c)
    idx=pd.period_range(min(income.index.min(),cash.index.min()),max(income.index.max(),cash.index.max()),freq='Q')
    income=income.reindex(idx);cash=cash.reindex(idx)
    def field(df,names):
        out=pd.Series(np.nan,index=df.index)
        for n in names:
            if n in df:out=out.fillna(df[n])
        return out
    revenue=field(income,['Revenue','OperatingRevenue'])
    op=field(income,['OperatingIncome','OperatingProfit'])
    cfo_ytd=field(cash,['CashFlowsFromOperatingActivities','NetCashInflowFromOperatingActivities'])
    # FinMind cash flows are YTD cumulative, unlike single-quarter income fields.
    cfo_q=cfo_ytd.groupby(cfo_ytd.index.year).diff()
    cfo_q.loc[idx.quarter==1]=cfo_ytd.loc[idx.quarter==1]
    rev_ttm=revenue.rolling(4,min_periods=4).sum();op_ttm=op.rolling(4,min_periods=4).sum();cfo_ttm=cfo_q.rolling(4,min_periods=4).sum()
    e=pd.DataFrame({'quarter_revenue':revenue,'quarter_cfo':cfo_q,'cfo_ytd':cfo_ytd,'revenue_ttm':rev_ttm,
                    'revenue_growth':rev_ttm/rev_ttm.shift(4)-1,'quarterly_growth':revenue/revenue.shift(4)-1,
                    'operating_margin':op_ttm/rev_ttm,'cfo_margin':cfo_ttm/rev_ttm},index=idx)
    e=e[e.quarter_revenue.notna()].copy()
    e['prior_release_growth']=e.revenue_growth.shift(1);e['prior_release_margin']=e.operating_margin.shift(1)
    deadlines=[]
    for p in e.index:
        if p.quarter==1:d=pd.Timestamp(p.year,5,15)
        elif p.quarter==2:d=pd.Timestamp(p.year,8,31 if 'KY' in name else 14)
        elif p.quarter==3:d=pd.Timestamp(p.year,11,14)
        else:d=pd.Timestamp(p.year+1,3,31)
        deadlines.append(d)
    e['assumed_deadline']=deadlines
    e['financial_event_date']=e.assumed_deadline+pd.to_timedelta(cfg['financial_deadline_buffer_days']+extra_days,unit='D')
    e['quarter_end']=e.index.to_timestamp(how='end').normalize()
    e['fundamental_release_key']=[symbol+'-'+str(p) for p in e.index]
    e['announcement_date_verified']=False
    e['financial_status']=np.where(e[['revenue_growth','quarterly_growth','operating_margin','cfo_margin']].notna().all(axis=1),'ok','incomplete')
    e['symbol']=symbol
    return e.reset_index(names='fiscal_quarter').sort_values('financial_event_date')

def financial_reason(row,entry,cfg):
    if row.get('financial_status')!='ok':return 'financial_data_unavailable'
    reasons=[]
    if not np.isfinite(row.get('financial_age_days',np.nan)) or row['financial_age_days']>cfg['maximum_financial_age_days']:reasons.append('financial_data_stale')
    keys=['revenue_growth','quarterly_growth','operating_margin','cfo_margin']
    if not all(np.isfinite(row.get(k,np.nan)) for k in keys):return 'financial_data_unavailable'
    if row['revenue_growth']<cfg['entry_revenue_growth']:reasons.append('revenue_growth_below_10pct')
    if row['quarterly_growth']<0:reasons.append('quarterly_revenue_decline')
    if row['operating_margin']<=0:reasons.append('operating_margin_nonpositive')
    if row['cfo_margin']<=0:reasons.append('operating_cashflow_nonpositive')
    for col,old,drop,reason in [('revenue_growth','prior_release_growth',cfg['exit_revenue_growth_drop'],'revenue_growth_drop_since_release'),('operating_margin','prior_release_margin',cfg['exit_operating_margin_drop'],'margin_drop_since_release')]:
        ref=row.get(old,np.nan)
        if np.isfinite(ref) and row[col]<=ref-drop+1e-12:reasons.append(reason)
    if entry:
        if row['revenue_growth']<=entry['entry_revenue_growth']-cfg['exit_revenue_growth_drop']+1e-12:reasons.append('revenue_growth_drop_since_entry')
        if row['operating_margin']<=entry['entry_operating_margin']-cfg['exit_operating_margin_drop']+1e-12:reasons.append('margin_drop_since_entry')
    return ';'.join(reasons)

def prepare(extra_days=0):
    cfg=json.loads((ROOT/'config.json').read_text());u=pd.read_csv(ROOT/'universe.csv',dtype={'symbol':str})
    benchmark=load_chart('0050');sector=load_chart('0052')
    end=min(pd.Timestamp(cfg['requested_end']),benchmark.index[-1],sector.index[-1])
    calendar=market_calendar(end)
    calendar=calendar[calendar>=pd.Timestamp(cfg['price_start'])]
    benchmark=benchmark.reindex(calendar);sector=sector.reindex(calendar)
    # ETF-specific suspensions must not remove stock-market sessions.
    benchmark['adj_close']=benchmark.adj_close.ffill();sector['adj_close']=sector.adj_close.ffill()
    macro=pd.read_csv(ROOT/'data/global_macro_vintages.csv',parse_dates=['macro_vintage_date'])
    macro['macro_available_date_tw']=macro.macro_vintage_date+pd.Timedelta(days=1)
    m=pd.merge_asof(pd.DataFrame({'signal_date':calendar}),macro.sort_values('macro_available_date_tw'),left_on='signal_date',right_on='macro_available_date_tw',direction='backward')
    m['funding_good']=m.macro_status.eq('vintage_verified')&m.macro_stress.le(cfg['entry_max_macro_stress'])
    frames=[];events=[];coverage=[];charts={'0050':benchmark,'0052':sector}
    for row in u.itertuples():
        try:
            chart=load_chart(row.symbol)
            if chart.index[-1]<end:raise ValueError('Does not reach common cutoff; unresolved terminal corporate action/suspension')
            e=financial_events(row.symbol,row.name,cfg,extra_days)
            if e.financial_status.eq('ok').sum()<8:raise ValueError('Insufficient complete financial quarters')
            d=chart.reindex(calendar).copy();d['observed_price']=d.close.gt(0)&d.open.gt(0)&d.adj_close.gt(0)&d.volume.gt(0)
            # Mark missing sessions at last known TR close; never invent an executable open.
            d['adj_mark']=d.adj_close.where(d.adj_close.gt(0)).ffill()
            d['previous_quote_close']=d.close.where(d.close.gt(0)).ffill().shift(1)
            d['opening_gap']=d.open/d.previous_quote_close-1
            charts[row.symbol]=d
            f=pd.merge_asof(pd.DataFrame({'signal_date':calendar}),e.sort_values('financial_event_date'),left_on='signal_date',right_on='financial_event_date',direction='backward')
            f['symbol']=row.symbol;f['name']=row.name;f['market']=row.market;f['industry']=row.industry
            f['financial_age_days']=(f.signal_date-f.financial_event_date).dt.days
            f['fundamental_good']=~f.apply(lambda x:bool(financial_reason(x.to_dict(),None,cfg)),axis=1)
            tr=d.adj_mark
            f['drawdown_126']=(tr/tr.rolling(126,min_periods=126).max()-1).to_numpy()
            relative=tr.pct_change(63,fill_method=None)-sector.adj_close.reindex(calendar).pct_change(63,fill_method=None)
            f['relative_63']=relative.to_numpy()
            f['technical_divergence']=f.drawdown_126.between(-.5,-.1)&f.relative_63.lt(0)
            turnover=(d.close*d.volume).where(d.observed_price,0)
            f['average_turnover_20']=turnover.rolling(20,min_periods=20).mean().to_numpy()
            observations=d.observed_price.astype(int).cumsum()
            f['tradable']=(d.observed_price&observations.ge(cfg['minimum_history_sessions'])&pd.Series(f.average_turnover_20.to_numpy(),index=calendar).ge(cfg['minimum_average_turnover_twd'])).to_numpy()
            f=f.merge(m,on='signal_date',how='left')
            # Monthly revenue is an auxiliary warning only, not a trading gate.
            monthly_path=RAW/f'{row.symbol}_monthly.json'
            if monthly_path.exists():
                monthly=pd.DataFrame(json.loads(monthly_path.read_text()).get('data',[]))
                if not monthly.empty:
                    monthly['period']=pd.to_datetime(dict(year=monthly.revenue_year,month=monthly.revenue_month,day=1))
                    monthly=monthly.sort_values('period').drop_duplicates('period',keep='last')
                    monthly['monthly_revenue_yoy']=monthly.revenue/monthly.revenue.shift(12)-1
                    monthly['monthly_assumed_available']=monthly.period+pd.offsets.MonthBegin(1)+pd.Timedelta(days=16)
                    f=pd.merge_asof(f.sort_values('signal_date'),monthly[['monthly_assumed_available','monthly_revenue_yoy']],left_on='signal_date',right_on='monthly_assumed_available',direction='backward')
            frames.append(f);events.append(e)
            coverage.append({'symbol':row.symbol,'name':row.name,'market':row.market,'status':'included','sessions':len(chart),'financial_ok_quarters':int(e.financial_status.eq('ok').sum()),'last_price':str(chart.index[-1].date())})
        except Exception as exc:coverage.append({'symbol':row.symbol,'name':row.name,'market':row.market,'status':'excluded','reason':str(exc)})
    coverage=pd.DataFrame(coverage);coverage.to_csv(ROOT/'results/data_coverage.csv',index=False,encoding='utf-8-sig')
    if len(frames)<30:raise ValueError('Fewer than 30 usable stocks; inspect data_coverage.csv')
    panel=pd.concat(frames,ignore_index=True)
    common=panel.fundamental_good & panel.funding_good & panel.tradable
    panel['selection_score']=np.nan
    for key,weight in [('revenue_growth',.5),('operating_margin',.25),('cfo_margin',.25)]:
        ranks=panel.loc[common].groupby('signal_date')[key].rank(pct=True)
        panel.loc[common,'selection_score']=panel.loc[common,'selection_score'].fillna(0)+ranks*weight
    panel=panel[panel.signal_date>='2015-12-30'].sort_values(['signal_date','symbol']).reset_index(drop=True)
    tag='' if extra_days==0 else f'_lag{extra_days}'
    panel.to_csv(ROOT/f'data/processed/signals{tag}.csv',index=False)
    pd.concat(events,ignore_index=True).to_csv(ROOT/f'data/processed/financial_events{tag}.csv',index=False)
    m.to_csv(ROOT/'data/processed/macro_availability.csv',index=False)
    metadata={'end':str(end.date()),'included_stocks':int(coverage.status.eq('included').sum()),'requested_stocks':len(u),'signal_rows':len(panel),'financial_dates_verified':False,'extra_financial_lag_days':extra_days}
    (ROOT/f'results/preparation{tag}.json').write_text(json.dumps(metadata,indent=2))
    print(metadata,flush=True)
    return panel,charts,calendar,end,cfg
if __name__=='__main__':prepare()
