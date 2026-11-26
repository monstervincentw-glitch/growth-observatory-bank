"""Bridge completed Sheets batches to the existing sealed continuation engine."""
import argparse,base64,csv,gzip,hashlib,io,json,math,subprocess,sys
from pathlib import Path
from datetime import datetime,timezone
from urllib.request import urlopen
import pandas as pd
import exchange_calendars as calendars
ROOT=Path(__file__).resolve().parents[1]

def save(path,data):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')

def fetch_snapshot():
    with urlopen((ROOT/'data/sheets-url.txt').read_text().strip(),timeout=45) as response:
        rows=list(csv.reader(io.StringIO(response.read(2_000_001).decode('utf-8-sig'))))
    from sheets_data import parse_envelope
    entry=parse_envelope(rows)
    return rows,entry

def eligible_version(versions,day,market):
    close=pd.Timestamp(day+('T13:30:00+08:00' if market=='TW' else 'T16:00:00-05:00'))
    eligible=[v for v in versions if pd.Timestamp(v['eligibleFrom'])<=close
              and (market!='US' or (v.get('financial',{}).get('max_input_filed')
                   and pd.Timestamp(v['financial']['max_input_filed'])<=pd.Timestamp(day)-pd.Timedelta(days=2)))]
    return max(eligible,key=lambda v:(v['period'],v['firstVerifiedAt'])) if eligible else None

def features(bars,benchmark,financial,market):
    ref=lambda d:next(b['adjClose'] for b in reversed(benchmark) if b['date']<=d)
    last,prior=bars[-1],bars[-64]
    result=dict(history_sessions=len(bars),dollar_volume=sum(b['close']*b['volume'] for b in bars[-20:])/20,
                drawdown_126=last['adjClose']/max(b['adjClose'] for b in bars[-126:])-1,
                relative_63=last['adjClose']/prior['adjClose']-ref(last['date'])/ref(prior['date']))
    if market=='US':
        cap=financial.get('shares')
        result['market_cap_estimate']=cap*last['close'] if cap else None
        result['price_sales']=result['market_cap_estimate']/financial['revenue_ttm'] if cap and financial.get('revenue_ttm') else None
    return result

def build(inputs,output,rows,entry,quotes):
    snapshot=entry['snapshot'];now=datetime.now(timezone.utc).isoformat();batch=entry['batch']
    attempt=dict(status='published',batch=batch,at=now,detail='Completed shared Sheets batch',runId='sheets-continuation')
    save(inputs/'release.json',dict(snapshot=snapshot,rows=rows,attempt=attempt))
    save(inputs/'attempts.json',{m:dict(attempt,market=m) for m in ['US','TW']})
    feed=inputs/'etf-inputs'/f"{snapshot['markets']['US']['macro']['marketAsOf']}.json"
    if feed.exists():
        data=json.loads(feed.read_text());data['batch']=batch;save(feed,data)
    errors={}
    for market in ['US','TW']:
      try:
        build_market(inputs,output,entry,quotes,market,now,batch)
      except Exception as exc:
        errors[market]=str(exc)
    if errors:raise ValueError(json.dumps(errors))
    return {m:b['macro']['marketAsOf'] for m,b in snapshot['markets'].items()}

def build_market(inputs,output,entry,quotes,market,now,batch):
        snapshot=entry['snapshot']
        block=snapshot['markets'][market];cutoff=block['macro']['marketAsOf']
        manifest=output/'primary'/market/'release.json'
        anchor=json.loads(manifest.read_text())['through']
        registry=json.loads((inputs/'financial-versions'/f'{market}.json').read_text())
        priorpath=inputs/'observed-primary-inputs'/market/f'{anchor}.json'
        if not priorpath.exists():priorpath=inputs/'primary-inputs'/market/f'{anchor}.json'
        if not priorpath.exists():priorpath=inputs/'recovered-primary-inputs'/market/f'{anchor}.json'
        if not priorpath.exists() and (inputs/'observed-primary-inputs'/market/f'{anchor}.json.gz').exists():priorpath=inputs/'observed-primary-inputs'/market/f'{anchor}.json.gz'
        # Existing close-eligible archive supplies missing/filtered financial rows.
        priors=(json.loads(gzip.decompress(priorpath.read_bytes())) if priorpath.suffix=='.gz' else json.loads(priorpath.read_text()))['stocks'] if priorpath.exists() else {}
        if market=='TW' and not priors:
            p=inputs/'observed-primary-inputs'/market/f'{anchor}.json'
            if p.exists():priors=json.loads(p.read_text())['stocks']
        sessions=calendars.get_calendar('XNYS' if market=='US' else 'XTAI').sessions_in_range(anchor,cutoff)
        for date in sessions:
            day=str(date.date());stocks={}
            for stock in block['stocks']:
                symbol=stock['symbol']
                if stock.get('marketEvent'):
                    stocks[symbol]=dict(stock);continue
                v=eligible_version(list(registry.get(symbol,{}).get('versions',{}).values()),day,market)
                f=dict(v['financial']) if v else dict(priors.get(symbol,{}).get('financial',{}))
                current=stock['financial']
                # US SEC inputs carry their actual filing date and original two-day lag.
                if (market=='US' and current.get('max_input_filed')
                        and pd.Timestamp(current['max_input_filed'])<=date-pd.Timedelta(days=2)
                        and (not f or current['max_input_filed']>f.get('max_input_filed',''))):f=dict(current)
                if f.get('latest_period'):f['financial_age_days']=(date-pd.Timestamp(f['latest_period'])).days
                if not f:raise ValueError(f'{market} {day} {symbol}: no saved eligible financial version')
                bars=[b for b in quotes['bars'][market][symbol] if b['date']<=day]
                if not bars or bars[-1]['date']!=day:raise ValueError(f'{symbol}: absent session {day}')
                benchmark=quotes['benchmarks'][market][stock['benchmark']]
                ft=features(bars,benchmark,f,market)
                threshold=1e7 if market=='US' else 3e8
                flags=dict(tradable=ft['history_sessions']>=252 and ft['dollar_volume']>=threshold,
                           divergence=-.5<=ft['drawdown_126']<=-.1 and ft['relative_63']<0)
                if market=='US':
                    flags['tradable']=bool(flags['tradable'] and (ft.get('market_cap_estimate') or 0)>=1e9
                        and ft.get('price_sales') is not None and .05<=ft['price_sales']<=200
                        and f.get('growth_acceleration') is not None and math.isfinite(f['growth_acceleration'])
                        and f.get('shares_date') and (date-pd.Timestamp(f['shares_date'])).days<=180)
                stocks[symbol]=dict(financial=f,financialVersion=v,bars=bars,features=ft,evaluation=flags,
                                    industry=stock['industry'],name=stock['name'])
            payload=dict(schema=1,market=market,cutoff=day,batch=batch,status='POINT_IN_TIME_INPUT_VERIFIED',
                         observedAt=now,recoveredAt=now,stocks=stocks,
                         recoveryBasis='SAVED_ELIGIBLE_FINANCIAL_VERSIONS_AND_SESSION_PRICES',
                         sourceSnapshotSHA256=entry['checksum'])
            from primary_live import point_in_time
            gaps=point_in_time(payload,market)
            if gaps:raise ValueError(f'{market} {day}: saved financial availability gaps {gaps}')
            dest=inputs/'observed-primary-inputs'/market/f'{day}.json'
            if not dest.exists() and not dest.with_suffix('.json.gz').exists():
                dest.parent.mkdir(parents=True,exist_ok=True)
                dest.with_suffix('.json.gz').write_bytes(gzip.compress(json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode(),mtime=0))

def main():
    p=argparse.ArgumentParser();p.add_argument('inputs',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--runtime');p.add_argument('--snapshot',type=Path);p.add_argument('--node',default='node');a=p.parse_args()
    rows,entry=fetch_snapshot()
    attempt=dict(status='published',batch=entry['batch'],at=entry['snapshot']['generatedAt'],detail='Completed shared Sheets batch',runId='sheets-continuation')
    save(a.inputs/'release.json',dict(snapshot=entry['snapshot'],rows=rows,attempt=attempt))
    save(a.inputs/'attempts.json',{m:dict(attempt,market=m) for m in ['US','TW']})
    # Same successful close is metadata-only: no quote, financial or FRED requests.
    if all((a.output/'primary'/m/'release.json').exists() and json.loads((a.output/'primary'/m/'release.json').read_text())['through']>=b['macro']['marketAsOf'] for m,b in entry['snapshot']['markets'].items()) and json.loads((a.output/'etf/manifest.json').read_text())['through']>=entry['snapshot']['markets']['US']['macro']['marketAsOf']:return
    import tempfile
    with tempfile.TemporaryDirectory() as temp:
        snap=Path(temp)/'snapshot.json';save(snap,entry['snapshot'])
        command=[a.node,str(ROOT/'engine/continuation-quotes.mjs'),str(snap),str(a.inputs)]
        if a.runtime:command.append(a.runtime)
        subprocess.run(command,cwd=ROOT,check=True,timeout=1200)
    print(build(a.inputs,a.output,rows,entry,json.loads((a.inputs/'continuation-quotes.json').read_text())))
if __name__=='__main__':main()
