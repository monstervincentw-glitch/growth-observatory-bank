"""Public VGT CSV views for the existing research workbook's IMPORTDATA tabs."""
import csv
import io
import json
from pathlib import Path

from publication import decode

SUMMARY = [('symbol','標的'),('start_year','起始年'),('variant','方案'),('through','績效截止日'),
           ('cumulative','累積報酬'),('cagr','年化報酬'),('mdd','最大回撤'),
           ('peak','回撤高點'),('trough','回撤低點'),('recovered','回本日'),
           ('buy_count','買入次數'),('sell_count','賣出次數'),
           ('transaction_cost_initial_fraction','直接成本占初始資金'),('batch','績效批次')]
TRADE_KEYS = ['start_year','variant','date','signal_date','symbol','side',
              'notional_per_initial_dollar','cost_per_initial_dollar','state','fill','adjusted_price']
TRADE_NAMES = ['起始年','方案','成交日','訊號版本日','標的','買賣',
               '成交額占初始資金','成本占初始資金','資金狀態','成交時點','含息調整成交價']

def write_csv(path, header, rows):
    output=io.StringIO(newline='')
    writer=csv.writer(output)
    writer.writerow(header)
    writer.writerows(rows)
    text=output.getvalue()
    if not path.exists() or path.read_text()!=text:
        path.write_text(text,encoding='utf-8',newline='')

def export_vgt(output):
    output=Path(output)
    folder=output/'etf'
    view=decode(json.loads((folder/'release.json').read_text()))
    summary=[r for r in view['summary'] if r['symbol']=='VGT']
    if not summary:
        return
    if len(summary)!=84:
        raise ValueError('VGT export requires all 21 years and four plans')
    write_csv(folder/'vgt-performance.csv',[label for _,label in SUMMARY],
              [[(r|{'batch':view['batch']}).get(key) for key,_ in SUMMARY] for r in summary])
    curve_path=folder/'vgt-nav-2006.csv'
    if not curve_path.exists():
        raise ValueError('VGT historical curve must be seeded before incremental export')
    with curve_path.open(newline='',encoding='utf-8') as handle:
        reader=csv.reader(handle)
        header=next(reader)
        nav={row[0]:row[1:5] for row in reader}
    trades_path=folder/'vgt-trades.json'
    trades=json.loads(trades_path.read_text()) if trades_path.exists() else []
    indexed={tuple(str(r.get(k,'')) for k in TRADE_KEYS[:6]):r for r in trades}
    for path in sorted((output/'etf-ledger').glob('*.json')):
        ledger=json.loads(path.read_text())
        for row in ledger.get('daily',[]):
            if row.get('symbol')!='VGT' or row.get('year')!=2006:
                continue
            day=row['date'][:10]
            nav.setdefault(day,[None]*4)['ABCD'.index(row['plan'])]=row['nav']
        for row in ledger.get('trades',[]):
            if row.get('cohort_symbol',row.get('symbol'))!='VGT':
                continue
            item=row|{'start_year':row.get('year',row.get('start_year')),
                      'variant':row.get('plan',row.get('variant')),'date':row['date'][:10]}
            indexed[tuple(str(item.get(k,'')) for k in TRADE_KEYS[:6])]=item
    ordered=sorted(indexed.values(),key=lambda r:(int(r['start_year']),r['variant'],r['date'],r['symbol'],r['side']))
    if any(any(value is None for value in values) for values in nav.values()):
        raise ValueError('VGT daily export is missing a plan')
    write_csv(curve_path,header,[[day,*values] for day,values in sorted(nav.items())])
    write_csv(folder/'vgt-trades.csv',TRADE_NAMES,[[r.get(k) for k in TRADE_KEYS] for r in ordered])
    trades_path.write_text(json.dumps(ordered,allow_nan=False,separators=(',',':'))+'\n')
