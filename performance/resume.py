"""Resume an already-verified test checkpoint without replaying its historical accounts."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import tempfile

import pandas as pd

from market_inputs import SOURCE, read_market
from run import (IDS, advisor, advance_risk, close, etf_position, fund, fund_inputs,
                 fund_position, fund_run, js, read_fund_state)

def update_risk(old, new):
    risk = old['risk'].copy()
    statistics = advance_risk(risk,new)
    risk.update(peak_nav=statistics['peak_nav'],max_drawdown=statistics['mdd'],end=str(new.index[-1].date()))
    return risk,{k:float(statistics[k]) for k in ('cumulative','cagr','mdd')}

def validate_anchor(checkpoint, current):
    for key,frozen in checkpoint['market'].items():
        if frozen is None and pd.isna(current[key]):
            continue
        if isinstance(frozen,(int,float)) and not isinstance(frozen,bool):
            close(frozen,current[key],atol=1e-10)
        elif frozen != current[key]:
            raise ValueError(f'Checkpoint observation changed: {key}')

def one_etf(code, old, market, signals):
    price = market['QQQ'][0][['tr_open','tr_close']].join(
        market['SHY'][0][['tr_open','tr_close']].add_prefix('shy_'))
    end = pd.Timestamp(old['risk']['end'])
    validate_anchor(old,price.loc[end].to_dict())
    if old.get('signal') and js(signals.loc[end].to_dict()) != old['signal']:
        raise ValueError('Checkpoint ETF signal changed')
    if price.index[-1] <= end:
        return None
    newer = signals.loc[signals.index>end]
    if newer.score.isna().any():
        raise ValueError('Missing ETF signal evidence')
    symbol = old.get('asset','QQQ')
    daily,orders,state = advisor.replay(symbol,price,signals,end+pd.Timedelta(days=1),code,False,checkpoint=old['state'])
    state.update(buy_count=old['state']['buy_count']+int(orders.side.eq('BUY').sum()),
                 sell_count=old['state']['sell_count']+int(orders.side.eq('SELL').sum()),
                 direct_cost=old['state']['direct_cost']+float(orders.cost_per_initial_dollar.sum()))
    risk,performance = update_risk(old,daily.nav)
    if (daily.index[-1]-pd.Timestamp(risk['start'])).days+1 < 365:
        performance['cagr'] = None
    info = dict(state=state,risk=risk,market=price.iloc[-1].to_dict(),signal=signals.iloc[-1].to_dict(),
                performance=performance,position=etf_position(daily.iloc[-1],state))
    if info['position']['held']=='QQQ':
        info['position']['held']=symbol
    return info, daily.rename_axis('date').reset_index(), orders

def one_fund(isin, code, old, market, funds, macro, folder, catalog):
    shy = market['SHY'][0]
    frame,fx,nav,calendar,flags = fund_inputs(isin,funds,shy,macro,folder)
    end = pd.Timestamp(old['risk']['end'])
    validate_anchor(old,frame.loc[end].to_dict())
    future = frame.loc[frame.index>end]
    if future.empty:
        return None
    if future.score.isna().any():
        raise ValueError('Missing fund signal evidence')
    ledger,_,_,_,state,daily,orders = fund_run(future,isin,catalog.loc[isin],code,(calendar,shy.index,nav.index),
                                               read_fund_state(old['state']))
    risk,performance = update_risk(old,ledger.nav/fund.INITIAL)
    info = dict(state=state,risk=risk,market=frame.iloc[-1].to_dict(),performance=performance,
                position=fund_position(daily.iloc[-1]),quality=flags,
                fx=fx.iloc[-1].to_dict())
    return info,daily,orders

def resume(folder, from_dir, output, stage):
    if output.exists():
        raise FileExistsError('Use a new destination for each immutable test batch')
    manifest,market,funds,macro,_ = read_market(folder,advisor,True)
    signals = advisor.aligned_signals(macro,market['QQQ'][0].index,release_lag=1)
    catalog = pd.read_csv(SOURCE/'fund_catalog.csv',keep_default_na=False).set_index('ISIN')
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as temp:
        draft = Path(temp)/'batch'
        draft.mkdir()
        states,seen = {},{}
        for asset in ('QQQ',*IDS):
            for code in advisor.METHODS:
                key = f'{asset}_{code}'
                old = json.loads((from_dir/f'{key}_{stage}.json').read_text())
                if (old['asset'],old['plan'],old['version']) != (asset,code,1):
                    raise ValueError(f'Invalid checkpoint identity {key}')
                seen[key] = old['risk']['end']
                item = (one_etf(code,old,market,signals) if asset=='QQQ'
                        else one_fund(asset,code,old,market,funds,macro,folder,catalog))
                if item is None:
                    new = old
                else:
                    info,daily,orders = item
                    new = js({**old,**info})
                    daily.to_csv(draft/f'{key}_account.csv',index=False)
                    orders.to_csv(draft/f'{key}_trades.csv',index=False)
                    states[key] = new['risk']['end']
                (draft/f'{key}_latest.json').write_text(json.dumps(new,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        meta = dict(status='TEST_UPDATED' if states else 'NO_NEW_DATA', prior_ends=seen, new_ends=states,
                    updated_at=datetime.now(timezone.utc).isoformat(),
                    source_sha256=sha256((folder/'manifest.json').read_bytes()).hexdigest())
        (draft/'batch.json').write_text(json.dumps(meta,indent=2)+'\n')
        draft.rename(output)
    return meta

if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('source',type=Path)
    p.add_argument('checkpoints',type=Path)
    p.add_argument('output',type=Path)
    p.add_argument('--stage',choices=['archive','latest'],default='latest')
    a = p.parse_args()
    print(json.dumps(resume(a.source,a.checkpoints,a.output,a.stage),indent=2))
