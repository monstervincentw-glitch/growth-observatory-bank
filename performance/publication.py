"""Isolated performance publication. One atomic Sheets mutation per complete batch."""
import argparse
import base64
from datetime import datetime,timezone
import gzip
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import re

OFFICIAL_RESEARCH_ID='1I9Alvyhg4UZXi-AdylJN0pjiLHSMzZUleFK0vsXG1K0'
DATA_TABS=('etf_performance','etf_accounts','etf_trades','performance_release','performance_runs')
HEADER=['batch','part','parts','sha256','payload']
RUN_HEADER=['updated_at','scope','status','batch','through','detail']
ETF_HEADERS=['batch','symbol','start_year','variant','through','cumulative','cagr','mdd','peak','trough','recovered',
 'peak_to_recovery_calendar_days','trough_to_recovery_calendar_days','buy_count','sell_count','transaction_cost_initial_fraction']
DAY_HEADERS=['batch','symbol','year','plan','date','nav','cash','target_value','shy_value','score','state']
TRADE_HEADERS=['batch','cohort_symbol','year','plan','date','signal_date','symbol','side','notional_per_initial_dollar','cost_per_initial_dollar']
EXPECT_COUNTS={'AGG':84,'HYG':80,'IEF':84,'JNK':80,'LQD':84,'QQQ':84,'SOXX':84,'SPY':84,'TLT':84,'VOO':68,'VT':76,'XLK':84}
DATE=r'\d{4}-\d{2}-\d{2}'

def rows(columns,records):
    return [list(columns),*[[r.get(k) for k in columns] for r in records]]

def validate_view(data):
    if set(data)!={'schema','scope','batch','through','calculated_at','summary'} or data['schema']!=1 or data['scope']!='ETF':
        raise ValueError('Performance schema mismatch')
    if not re.fullmatch(r'etf-[a-f0-9]{16}',data['batch']) or not re.fullmatch(DATE,data['through']):
        raise ValueError('Invalid batch identity')
    datetime.fromisoformat(data['calculated_at'])
    keys=set()
    counts={}
    for row in data['summary']:
        sym,year,variant=row['symbol'],row['start_year'],row['variant']
        if (sym,year,variant) in keys or variant not in 'ABCD' or not isinstance(year,int) or not 2006<=year<=2026+20:
            raise ValueError('Duplicate or invalid performance key')
        keys.add((sym,year,variant))
        counts[sym]=counts.get(sym,0)+1
        if row['through']!=data['through'] or row.get('status')!='VERIFIED':
            raise ValueError('Mixed or unverified performance results')
        for k in ('cumulative','mdd'):
            if not isinstance(row[k],(int,float)) or not math.isfinite(row[k]):
                raise ValueError('Invalid performance number')
        if row['cagr'] is not None and not math.isfinite(row['cagr']):
            raise ValueError('Invalid CAGR')
        if row['cumulative']<=-1 or not -1<row['mdd']<=0:
            raise ValueError('Performance value out of range')
    if counts!=EXPECT_COUNTS or any({v for s,y,v in keys if s==sym and y==year}!=set('ABCD') for sym,year in {(s,y) for s,y,_ in keys}):
        raise ValueError('Incomplete ETF cohort set')
    return data

def encode(data):
    validate_view(data)
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    payload=base64.b64encode(gzip.compress(raw,mtime=0)).decode()
    parts=[payload[i:i+18000] for i in range(0,len(payload),18000)]
    return [HEADER,*[[data['batch'],i,len(parts),sha256(raw).hexdigest(),part] for i,part in enumerate(parts,1)]]

def decode(table):
    if not 2<=len(table)<=120 or table[0]!=HEADER:
        raise ValueError('Incomplete release header')
    batch,_,n,digest,_=table[1]
    if len(table)!=int(n)+1 or not re.fullmatch(r'[a-f0-9]{64}',digest):
        raise ValueError('Incomplete release chunks')
    body=[]
    for i,(b,j,count,h,p) in enumerate(table[1:],1):
        if (b,int(j),int(count),h)!=(batch,i,int(n),digest) or len(p)>18000:
            raise ValueError('Mixed release chunks')
        body.append(p)
    from io import BytesIO
    with gzip.GzipFile(fileobj=BytesIO(base64.b64decode(''.join(body),validate=True))) as f:
        raw=f.read(4_000_001)
    if len(raw)>4_000_000 or sha256(raw).hexdigest()!=digest:
        raise ValueError('Performance hash mismatch')
    result=validate_view(json.loads(raw))
    if result['batch']!=batch:
        raise ValueError('Performance batch mismatch')
    return result

def prepare(batch_dir):
    meta=json.loads((batch_dir/'manifest.json').read_text())
    raw=(batch_dir/'results.json').read_bytes()
    if meta['status']!='VERIFIED' or sha256(raw).hexdigest()!=meta['results_sha256']:
        raise ValueError('Only a complete validated batch can be published')
    result=json.loads(raw)
    batch=meta['batch']
    view=dict(schema=1,scope='ETF',batch=batch,through=meta['through'],
              calculated_at=meta['calculated_at'],summary=result['summary'])
    tables={
      'etf_performance':rows(ETF_HEADERS,[r|{'batch':batch} for r in result['summary']]),
      'etf_accounts':rows(DAY_HEADERS,[r|{'batch':batch,'date':r['date'][:10]} for r in result['daily']]),
      'etf_trades':rows(TRADE_HEADERS,[r|{'batch':batch} for r in result['trades']]),
      'performance_release':encode(view),
      'performance_runs':[RUN_HEADER,[meta['calculated_at'],'ETF','VERIFIED',batch,meta['through'],'All 12 ETF products; FSPTX and VITAX remain archived.']],
    }
    return view,tables

def cells(row):
    return {'values':[{} if x is None else {'userEnteredValue':{'boolValue' if isinstance(x,bool) else
        'numberValue' if isinstance(x,(int,float)) else 'stringValue':x}} for x in row]}

def get_tabs(book):
    found={}
    for item in book['sheets']:
        p=item['properties']
        if p['title'] in DATA_TABS:
            found[p['title']]=p
    if set(found)!=set(DATA_TABS):
        raise ValueError('Isolated performance tab set unavailable')
    return found

def replace_table(tab,table):
    grid=tab['gridProperties']
    if len(table)>grid['rowCount'] or len(table[0])>grid['columnCount']:
        raise ValueError(f"Performance tab {tab['title']} has insufficient allocated grid")
    return {'updateCells':{'range':{'sheetId':tab['sheetId'],'startRowIndex':0,'endRowIndex':grid['rowCount'],
         'startColumnIndex':0,'endColumnIndex':len(table[0])},'rows':[cells(r) for r in table],'fields':'userEnteredValue'}}

def requests_for(book,old,view,tables):
    ids=get_tabs(book)
    if old is not None:
        if view['through']<old['through'] or (view['through']==old['through'] and view['batch']!=old['batch']):
            raise ValueError('Performance batch would overwrite the latest verified date')
        if old['batch']==view['batch']:
            return []
    request=[replace_table(ids[n],tables[n]) for n in ('etf_performance','performance_release')]
    for n in ('etf_accounts','etf_trades','performance_runs'):
        if len(tables[n])>1:
            request.append({'appendCells':{'sheetId':ids[n]['sheetId'],'rows':[cells(r) for r in tables[n][1:]],'fields':'userEnteredValue'}})
    return request

def session(service_account):
    from google.auth.transport.requests import AuthorizedSession
    from google.oauth2.service_account import Credentials
    return AuthorizedSession(Credentials.from_service_account_info(service_account,
        scopes=['https://www.googleapis.com/auth/spreadsheets']))

def api_get(client,url,**kwargs):
    response=client.get(url,timeout=30,**kwargs)
    response.raise_for_status()
    return response.json()

def api_base(sheet_id):
    if not re.fullmatch(r'[A-Za-z0-9_-]{20,}',sheet_id):
        raise ValueError('Invalid isolated performance spreadsheet ID')
    return f'https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}'

def publish(client,sheet_id,batch_dir):
    if sheet_id==OFFICIAL_RESEARCH_ID:
        raise ValueError('Use a separate performance workbook')
    url=api_base(sheet_id)
    book=api_get(client,url,params={'fields':'spreadsheetId,sheets(properties)'})
    view,tables=prepare(batch_dir)
    old_rows=api_get(client,url+'/values/performance_release!A1:E120',params={'valueRenderOption':'UNFORMATTED_VALUE'}).get('values',[])
    old=decode(old_rows) if len(old_rows)>1 else None
    requests=requests_for(book,old,view,tables)
    if not requests:
        return 'NO_NEW_DATA'
    response=client.post(url+':batchUpdate',json={'requests':requests},timeout=50)
    response.raise_for_status()
    actual=api_get(client,url+'/values/performance_release!A1:E120',params={'valueRenderOption':'UNFORMATTED_VALUE'})['values']
    if decode(actual)!=view:
        raise ValueError('Performance readback mismatch')
    return 'VERIFIED'

def record_attempt(client,sheet_id,status,through,detail):
    if sheet_id==OFFICIAL_RESEARCH_ID:
        raise ValueError('Use a separate performance workbook')
    url=api_base(sheet_id)
    book=api_get(client,url,params={'fields':'spreadsheetId,sheets(properties)'})
    table=get_tabs(book)['performance_runs']
    data=[datetime.now(timezone.utc).isoformat(),'ETF',status,None,through,detail]
    response=client.post(url+':batchUpdate',json={'requests':[{'appendCells':{'sheetId':table['sheetId'],
        'rows':[cells(data)],'fields':'userEnteredValue'}}]},timeout=20)
    response.raise_for_status()

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('batch',type=Path)
    p.add_argument('out',type=Path)
    args=p.parse_args()
    _,tables=prepare(args.batch)
    args.out.write_text(json.dumps(tables,ensure_ascii=False,allow_nan=False)+'\n')
