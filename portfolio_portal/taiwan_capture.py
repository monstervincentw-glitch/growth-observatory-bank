"""Bounded late-day quote recovery, owned by the existing research worker."""
from copy import deepcopy
from datetime import datetime
import json
import math
from zoneinfo import ZoneInfo

SLOTS = ((16, 30), (17, 30), (18, 30), (21, 27))
HEADERS = ['market', 'session_date', 'slot', 'status', 'completed', 'expected', 'detail', 'at', 'payload']
SOURCE = 'https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes'


def slot_at(clock):
    local = clock.astimezone(ZoneInfo('Asia/Taipei'))
    if local.weekday() >= 5:
        return None
    due = [s for s in SLOTS if (local.hour, local.minute) >= s]
    return f'{due[-1][0]:02d}:{due[-1][1]:02d}' if due else None


def next_slot(clock):
    local = clock.astimezone(ZoneInfo('Asia/Taipei'))
    later = [s for s in SLOTS if (local.hour, local.minute) < s]
    return local.replace(hour=later[0][0], minute=later[0][1], second=0, microsecond=0).timestamp() if later else 0


def valid_quote(q, day):
    return (q.get('priceDate') == day and q.get('currency') == 'TWD'
            and type(q.get('close')) in (int, float) and math.isfinite(q['close']) and q['close'] > 0)


def merge_quotes(stocks, live_rows, previous, official, day, at):
    """Keep verified same-session prices; fill only missing symbols."""
    symbols = {s['symbol'] for s in stocks}
    quotes = {s: deepcopy(q) for s, q in (previous or {}).get('quotes', {}).items()
              if s in symbols and valid_quote(q, day)}
    live = {}
    for row in live_rows:
        if row.get('market') == 'TW':
            live.setdefault(str(row.get('symbol')), []).append(row)
    for s in symbols - quotes.keys():
        rows = live.get(s, [])
        if len(rows) != 1:
            continue
        q = rows[0]
        stamp = str(q.get('quote_time', ''))
        try:
            price = float(q.get('price', ''))
        except (ValueError, TypeError):
            continue
        if (q.get('status') == 'OK' and q.get('currency') == 'TWD'
                and stamp[:10] == day and '13:25:00' <= stamp[11:19] <= '13:40:00'
                and math.isfinite(price) and price > 0):
            quotes[s] = dict(close=price, priceDate=day, currency='TWD', source='GOOGLEFINANCE', observedAt=at)
    otc = {s['symbol'] for s in stocks if s.get('ticker', '').endswith('.TWO')}
    for s in (symbols - quotes.keys()) & otc:
        rows = [r for r in official if str(r.get('SecuritiesCompanyCode', '')).strip() == s]
        if len(rows) != 1:
            continue
        row = rows[0]
        date = str(row.get('Date', '')).replace('/', '').replace('-', '')
        if len(date) == 7 and date.isdigit():
            date = f'{int(date[:3]) + 1911}-{date[3:5]}-{date[5:]}'
        elif len(date) == 8 and date.isdigit():
            date = f'{date[:4]}-{date[4:6]}-{date[6:]}'
        try:
            price = float(str(row.get('Close', '')).replace(',', ''))
        except (ValueError, TypeError):
            continue
        if date == day and math.isfinite(price) and price > 0:
            quotes[s] = dict(close=price, priceDate=day, currency='TWD', source='TPEx official daily quotes', observedAt=at)
    missing = sorted(symbols - quotes.keys())
    sources = {}
    for q in quotes.values():
        sources[q['source']] = sources.get(q['source'], 0) + 1
    return dict(schema=1, market='TW', day=day,
                status='PARTIAL_REFERENCE_QUOTES' if missing else 'REFERENCE_QUOTES_COMPLETE',
                expected=len(symbols), succeeded=len(quotes), inactive=[], missing=missing,
                sources=sources, quotes=quotes, capturedAt=at)


def recover_quotes(book, block, published, get_json, ensure_tab, write_rows, clock=None):
    """One persistent attempt per due slot, even after cold starts."""
    clock = clock or datetime.now(ZoneInfo('Asia/Taipei'))
    local = clock.astimezone(ZoneInfo('Asia/Taipei'))
    day, slot = local.date().isoformat(), slot_at(clock)
    if not slot or block['macro']['marketAsOf'] >= day or (published and published['day'] >= day):
        return published
    ensure_tab(book, 'quote_capture_state', HEADERS, 10)
    rows = book.values_get('quote_capture_state!A1:I2').get('values', [])
    previous = None
    if len(rows) > 1 and len(rows[1]) >= 9 and rows[1][1] == day:
        old = rows[1]
        if old[2] >= slot:
            return published
        previous = json.loads(old[8]) if old[8] else None
    at = clock.isoformat()
    def record(status, detail='', batch=None):
        batch = batch or previous or {}
        write_rows(book, 'quote_capture_state!A2:I2', [['TW', day, slot, status,
                   len(batch.get('quotes', {})), len(block['stocks']), detail[:500], at,
                   json.dumps(batch, separators=(',', ':'))]])
    record('RUNNING')  # claim before network work; a restart must not repeat it
    batch = previous
    try:
        values = book.values_get('market_quotes!A1:H200').get('values', [])
        if not values or values[0] != ['market', 'symbol', 'google_ticker', 'price', 'quote_time', 'currency', 'status', 'exchange']:
            raise ValueError('Google quote headers mismatch')
        live = [dict(zip(values[0], r)) for r in values[1:]]
        batch = merge_quotes(block['stocks'], live, previous, [], day, at)
        record(batch['status'], ','.join(batch['missing']), batch)
        missing_otc = {s['symbol'] for s in block['stocks'] if s.get('ticker', '').endswith('.TWO')} & set(batch['missing'])
        if missing_otc:
            batch = merge_quotes(block['stocks'], live, batch, get_json(SOURCE), day, at)
        record(batch['status'], ','.join(batch['missing']), batch)
        # Partial collection is durable, but is never passed off as a completed strategy batch.
        if batch['missing']:
            return published
        current = book.values_get('market_latest!A1:I3').get('values', [])
        target = next((i for i, r in enumerate(current) if r and r[0] == 'TW'), None)
        if target is None:
            raise ValueError('Missing TW publication row')
        newer = json.loads(current[target][8])
        if newer['day'] >= day:
            return newer
        row = ['TW', day, batch['status'], batch['expected'], batch['succeeded'], 0, '', at,
               json.dumps(batch, separators=(',', ':'))]
        book.values_append('market_quote_batches!A:I', params={'valueInputOption': 'RAW'}, body={'values': [row]})
        write_rows(book, f'market_latest!A{target + 1}:I{target + 1}', [row])
        record('COMPLETE', batch=batch)
        return batch
    except Exception as error:
        record('FAILED', str(error), batch)
        print(json.dumps({'quote_capture': 'failed', 'day': day, 'slot': slot, 'error': str(error)[:300]}), flush=True)
        return published
