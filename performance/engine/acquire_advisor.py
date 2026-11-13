"""Fetch and freeze USD total-return inputs with provider timestamps."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen
import json
import sys
import time

SYMBOLS = 'QQQ XLK SOXX SPY VOO VT FSPTX VITAX AGG LQD IEF TLT HYG JNK SHY'.split()
START = int(datetime(2005, 12, 28, tzinfo=timezone.utc).timestamp())
END = int(datetime(2026, 11, 6, tzinfo=timezone.utc).timestamp())

def fetch(symbol, out):
    urls = [f'https://query{i}.finance.yahoo.com/v8/finance/chart/{symbol}?period1={START}&period2={END}&interval=1d&events=div%2Csplits%2CcapitalGains' for i in [1, 2]]
    if symbol == 'DEXTAUS':
        urls = ['https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXTAUS&cosd=2005-12-28&coed=2026-11-04']
    errors = []
    for url in urls:
        for attempt in range(3):
            try:
                with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=25) as response:
                    payload = response.read()
                suffix = 'csv' if symbol == 'DEXTAUS' else 'json'
                if suffix == 'json':
                    result = json.loads(payload)['chart']['result'][0]
                    assert result['meta']['symbol'] == symbol and len(result['timestamp']) >= 200
                else:
                    assert payload.startswith(b'observation_date,DEXTAUS')
                (out / f'{symbol}.{suffix}').write_bytes(payload)
                return dict(symbol=symbol,url=url,sha256=sha256(payload).hexdigest(),bytes=len(payload),retrieved_utc=datetime.now(timezone.utc).isoformat())
            except Exception as exc:
                errors.append(str(exc))
                time.sleep(attempt+1)
    return dict(symbol=symbol,errors=errors)

if __name__ == '__main__':
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=6) as workers:
        rows = list(workers.map(lambda s: fetch(s, out), [*SYMBOLS,'DEXTAUS']))
    (out / 'manifest.json').write_text(json.dumps(rows,indent=2))
    for row in rows:
        print(row['symbol'], row.get('bytes',row.get('errors')), flush=True)
