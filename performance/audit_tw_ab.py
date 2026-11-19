"""Offline audit of frozen A/B ledgers and actual B eligibility calls; no new data."""
import argparse
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import pickle
import sys

import numpy as np
import pandas as pd


def audit(panel, market_data, archive, output):
    import equity_ab
    engine_dir=Path(__file__).parent/'primary_engine/tw'
    sys.path.insert(0,str(engine_dir))
    import backtest as eng
    charts,calendar,end,cfg=market_data
    macro=pd.read_csv(archive/'results/tw/macro_weekly.csv',parse_dates=['macro_vintage_date']).set_index('macro_vintage_date')
    panel=panel.copy()
    panel['macro_stress']=panel.macro_vintage_date.map(macro.grouped_score)
    panel['macro_status']=panel.macro_vintage_date.map(macro.grouped_status).fillna('unknown')
    panel['funding_good']=panel.macro_status.eq('vintage_verified')&panel.macro_stress.le(1)
    common=panel.fundamental_good&panel.funding_good&panel.tradable
    panel['selection_score']=np.nan
    for key,weight in [('revenue_growth',.5),('operating_margin',.25),('cfo_margin',.25)]:
        ranks=panel.loc[common].groupby('signal_date')[key].rank(pct=True)
        panel.loc[common,'selection_score']=panel.loc[common,'selection_score'].fillna(0)+ranks*weight
    market=eng.Market(panel,charts,calendar,cfg)
    original=equity_ab.eligible_extras
    calls=[]
    def inspect_call(positions,rows,ranked,blocked,score_key,cfg,partial=False):
        selected,threshold=original(positions,rows,ranked,blocked,score_key,cfg,partial)
        context=sys._getframe(1).f_locals
        core=[s for s,p in positions.items() if p['position_type']=='core']
        missing=sum(not np.isfinite(rows[s].get(score_key,np.nan)) for s in core)
        reason=('CORE_NOT_FULL' if len(core)!=cfg['maximum_positions'] else
                'CORE_SCORE_MISSING' if missing else 'NO_STRICTLY_HIGHER_ELIGIBLE_SCORE' if not selected else 'ELIGIBLE')
        calls.append(dict(date=str(context['date'].date()),signalDate=str(context['pending_entry_signal'].date()),
            core=len(core),missingCoreScores=missing,cash=float(context['cash']),navOpen=float(context['nav_open']),
            threshold=float(threshold) if np.isfinite(threshold) else None,
            eligibleExtraCount=len(selected),reason=reason))
        return selected,threshold
    equity_ab.eligible_extras=inspect_call
    try:run=equity_ab.build_tw(eng)
    finally:equity_ab.eligible_extras=original
    cohorts=[]
    for year in range(2016,2027):
        calls.clear()
        daily,orders,closed,positions,rejects=run(market,f'{year}-01-01',end,'primary',extras=True)
        frozen=archive/'results/tw'
        comparisons={}
        for name,new in [('daily',daily),('orders',orders),('closed',closed),('positions',positions),('rejects',rejects)]:
            old=pd.read_csv(frozen/'B'/f'{year}_{name}.csv')
            if name=='daily':new=new.reset_index()
            # Dates are serialized by pandas exactly as in the frozen delivery.
            from io import StringIO
            regenerated=pd.read_csv(StringIO(new.to_csv(index=False)))
            pd.testing.assert_frame_equal(regenerated,old,check_exact=False,rtol=1e-12,atol=1e-7)
            a=(frozen/'A'/f'{year}_{name}.csv').read_bytes()
            b=(frozen/'B'/f'{year}_{name}.csv').read_bytes()
            comparisons[name]=dict(A_sha256=sha256(a).hexdigest(),B_sha256=sha256(b).hexdigest(),
                                    byteIdentical=a==b,originalEngineReplayMatched=True)
        cohorts.append(dict(year=year,sessions=len(daily),buys=int(orders.action.eq('BUY').sum()),
            sells=int(orders.action.eq('SELL').sum()),extras=int(orders.reason.eq('excess_cash_high_score').sum()),
            gateCalls=len(calls),gateReasons=dict(Counter(x['reason'] for x in calls)),
            coreFullCashGateCalls=[c for c in calls if c['core']==10],ledgers=comparisons))
        print(year,cohorts[-1]['gateReasons'],flush=True)
    result=dict(schema=1,status='REPLAY_AND_LEDGER_MATCHED',through=str(end.date()),
        cohortCount=11,cohorts=cohorts,rulesChanged=False,
        engineSha256={p.name:sha256(p.read_bytes()).hexdigest() for p in
                      (engine_dir/'backtest.py',engine_dir/'prepare.py',engine_dir/'config.json',Path(equity_ab.__file__))})
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('panel',type=Path);p.add_argument('market_data',type=Path)
    p.add_argument('archive',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    with a.market_data.open('rb') as f:market_data=pickle.load(f)
    audit(pd.read_pickle(a.panel),market_data,a.archive,a.output)
