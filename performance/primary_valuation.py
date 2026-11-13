"""Mark the sealed public simulation accounts using verified adjusted prices."""
from hashlib import sha256
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def history_for(block, observations, anchor, maximum):
    series = {}
    if block:
        for stock in block['stocks']:
            if (stock.get('integrity') and not stock.get('sourceError') and not stock.get('marketEvent')
                    and stock.get('priceValidation',{}).get('cutoff')==stock['priceDate']):
                series[stock['symbol']] = {bar['date']:bar['close'] for bar in stock['history']}
    for symbol, quote in (observations or {}).get('quotes',{}).items():
        if quote.get('priceDate')!=observations['day'] or quote.get('priceValidation',{}).get('cutoff')!=quote['priceDate']:
            continue
        newer = {bar['date']:bar['adjClose'] for bar in quote['bars'] if anchor<=bar['date']<=maximum}
        if anchor in newer and observations['day'] in newer:
            series[symbol] = newer
    return series


def mark_valuation(inputs, release, output, market):
    baseline = json.loads((ROOT/'data/tracking-baseline.json').read_text())
    anchor = baseline['date']
    portfolios = baseline[market]
    symbols = {p['symbol'] for a in portfolios for p in a['positions']}
    path = inputs/'market-observations'/market/'latest.json'
    observations = json.loads(path.read_text()) if path.exists() else None
    block = release['snapshot']['markets'][market]
    if observations and (observations['schema']!=1 or observations['market']!=market):
        raise ValueError('Market quote archive identity mismatch')
    maximum = max(block['macro']['marketAsOf'],(observations or {}).get('day',anchor))
    series = history_for(block,observations,anchor,maximum)
    days = set.intersection(*(set(series.get(symbol,{})) for symbol in symbols))
    days = sorted(d for d in days if anchor<d<=maximum)
    for symbol in symbols:
        prices = series.get(symbol,{})
        if anchor not in prices or any(not math.isfinite(prices[d]) or prices[d]<=0 for d in [anchor,*days]):
            raise ValueError(f'Missing or invalid sealed position prices for {symbol}')
    if not days:
        return None

    def value(account, day):
        positions = [p|{'markedValue':p['value']*series[p['symbol']][day]/series[p['symbol']][anchor]}
                     for p in account['positions']]
        return {'variant':account['variant'],'cash':account['cash'],
                'value':account['cash']+sum(p['markedValue'] for p in positions),'positions':positions}

    # Save the per-day valuation independently of the verified trading ledger.
    directory = output/'primary-marks'/market
    latest = directory/'latest.json'
    prior = json.loads(latest.read_text()) if latest.exists() else None
    if prior and days[-1]<=prior['through']:
        return prior['through']
    from production import save
    groups = {}
    for account in portfolios:
        groups.setdefault(str(account['year']),[]).append(account)
    for day in days:
        if prior and day<=prior['through']:
            continue
        prices = {symbol:series[symbol][day]/series[symbol][anchor] for symbol in sorted(symbols)}
        result = dict(schema=1,market=market,through=day,anchor=anchor,
            status='CONDITIONAL_FIXED_HOLDINGS',sourceBatch=(observations or {}).get('batch',release['batch']),
            priceHash=sha256(json.dumps(prices,sort_keys=True).encode()).hexdigest(),
            years={year:[value(a,day) for a in accounts] for year,accounts in groups.items()})
        save(directory/'sessions'/f'{day}.json',result)
    save(latest,result)
    return result['through']
