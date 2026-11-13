"""Capture original vendors' raw responses for the three-instrument pilot."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import pandas as pd

ROOT = Path(__file__).resolve().parent
ESUN = 'https://wealth.esunbank.com/api/client/Wealth/FundNavChart?sc_lang=zh-TW'
FRED = 'https://fred.stlouisfed.org/graph/fredgraph.csv'
ALFRED = 'https://alfred.stlouisfed.org/graph/alfredgraph.csv'
FUNDS = {'LU0056508442': 'AG26', 'LU0210536867': 'CCG8', 'TW000T1614Y4': '3303'}
MACRO = ('CPIAUCSL', 'FEDFUNDS', 'DFII10', 'NFCI')

def chart(raw, symbol):
    envelope = json.loads(raw)
    item, = envelope['chart']['result']
    assert item['meta']['symbol'] == symbol
    idx = pd.to_datetime(item['timestamp'], unit='s', utc=True).tz_convert('America/New_York').tz_localize(None).normalize()
    frame = pd.DataFrame(item['indicators']['quote'][0], index=idx)[['open','close']]
    frame['adj_close'] = item['indicators']['adjclose'][0]['adjclose']
    frame = frame.dropna()
    assert frame.index.is_monotonic_increasing and frame.index.is_unique and frame.gt(0).all().all()
    return frame

def fetch(task, folder):
    name, url, body = task
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(3):
        try:
            with urlopen(Request(url, data, {'User-Agent':'Mozilla/5.0','Content-Type':'application/json'}), timeout=20) as response:
                raw = response.read()
            break
        except OSError:
            if attempt == 2:
                raise
    (folder / name).write_bytes(raw)
    return name, {'url': url, 'request_body': body, 'sha256': sha256(raw).hexdigest(),
                  'retrieved_utc': datetime.now(timezone.utc).isoformat()}

def vintage(name, day):
    return (f'{name}_{day:%Y%m%d}.csv',
            ALFRED + '?' + urlencode(dict(id=name, vintage_date=day.date(), cosd='2025-01-01')), None)

def acquire(folder, last_close, nav_end, anchor):
    folder.mkdir(parents=True)
    overlap = anchor - pd.Timedelta(days=8)
    assert nav_end >= last_close >= anchor
    args = urlencode(dict(period1=int(overlap.timestamp()), period2=int((last_close+pd.Timedelta(days=1)).timestamp()),
                          interval='1d', events='div,splits,capitalGains'))
    tasks = [(f'{s}.json', f'https://query1.finance.yahoo.com/v8/finance/chart/{s}?{args}', None) for s in ('QQQ','SHY')]
    tasks += [(f'{i}.json', ESUN, dict(A=code, StartDate=overlap.strftime('%Y/%m/%d'),
                                      EndDate=nav_end.strftime('%Y/%m/%d'))) for i,code in FUNDS.items()]
    tasks.append(('DEXTAUS.csv', FRED + '?' + urlencode(dict(id='DEXTAUS', cosd=overlap.date(), coed=nav_end.date())), None))
    tasks += [vintage('DEXTAUS', day-pd.Timedelta(days=1)) for day in pd.date_range(anchor+pd.Timedelta(days=1),nav_end) if day.weekday() < 5]
    manifest = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        manifest.update(pool.map(lambda task: fetch(task, folder), tasks))
        dates = chart((folder/'QQQ.json').read_bytes(), 'QQQ').index
        complete = dates[(dates > anchor) & (dates.weekday == 4)]
        if len(dates) and dates[-1] == last_close:
            # Include short holiday weeks only once that Friday has elapsed.
            eligible = dates[(dates > anchor) & (dates.to_period('W-FRI').end_time.normalize() <= last_close)]
            complete = eligible.to_series().groupby(eligible.to_period('W-FRI')).max().tolist()
        macro_tasks = [vintage(series, day) for series in MACRO for day in [anchor,*complete] if day <= last_close]
        manifest.update(pool.map(lambda task: fetch(task, folder), macro_tasks))
    (folder/'manifest.json').write_text(json.dumps(dict(last_close=str(last_close.date()), nav_end=str(nav_end.date()),
        anchor=str(anchor.date()), responses=manifest), indent=2, ensure_ascii=False))

if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('folder', type=Path)
    p.add_argument('--last-close', type=pd.Timestamp, required=True)
    p.add_argument('--nav-end', type=pd.Timestamp, required=True)
    p.add_argument('--anchor', type=pd.Timestamp, default=pd.Timestamp('2026-11-04'))
    a = p.parse_args()
    acquire(a.folder, a.last_close, a.nav_end, a.anchor)
