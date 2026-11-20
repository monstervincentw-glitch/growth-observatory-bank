"""Owner-only GOOGLEFINANCE marks for display, separate from strategy inputs."""
from datetime import date, datetime, timezone
from decimal import Decimal
from core import money, portfolio_value

QUOTE_HEADERS = ['symbol', 'google_ticker', 'price', 'quote_time', 'currency', 'status']


def reference_valuation(account, block, rows, market, today=None):
    """Use a complete, newer, same-day USD quote set; never mix partial marks.

    GOOGLEFINANCE price is a delayed quote, not verified closing history.
    Returned marks are display-only: no NAV writes or strategy mutations.
    """
    original = portfolio_value(account, block)
    day = block['macro']['marketAsOf']
    fallback = (original, {'source': '研究收盤行情', 'date': day, 'fallback': False})
    if market != 'US' or not account['lots']:
        return fallback
    today = today or datetime.now(timezone.utc).date()
    if any(s.get('marketEvent') for s in block['stocks']
           if s['symbol'] in account['lots']):
        return fallback
    records = {}
    duplicates = set()
    for row in rows:
        symbol = row.get('symbol')
        if symbol in records:
            duplicates.add(symbol)
        records[symbol] = row
    marks = {}
    try:
        for symbol in account['lots']:
            row = records[symbol]
            if symbol in duplicates or row['status'] != 'OK' or row['currency'] != 'USD':
                return fallback
            stamp = datetime.strptime(row['quote_time'], '%Y-%m-%d %H:%M:%S')
            quoted = stamp.date()
            price = money(row['price'])
            if price <= 0 or not 0 <= (today - quoted).days <= 4:
                return fallback
            marks[symbol] = (price, quoted.isoformat())
    except (KeyError, ValueError, TypeError):
        return fallback
    days = {v[1] for v in marks.values()}
    if len(days) != 1 or next(iter(days)) <= day:
        return fallback
    quote_day = next(iter(days))
    stocks = {s['symbol']: s for s in block['stocks']}
    items = []
    total = Decimal('0')
    for symbol, lots in account['lots'].items():
        price = marks[symbol][0]
        qty = sum((l['quantity'] for l in lots), Decimal('0'))
        cost = sum((l['quantity'] * l['unit_cost'] for l in lots), Decimal('0'))
        value = qty * price
        total += value
        # Preserve original financial and signal provenance for the risk UI.
        stock = stocks.get(symbol, {'symbol': symbol, 'integrity': False})
        items.append({'symbol': symbol, 'quantity': qty, 'price': price,
                      'value': value, 'cost': cost, 'pnl': value - cost,
                      'lots': lots, 'stock': stock})
    return ({'cash': account['cash'], 'market_value': total,
             'nav': account['cash'] + total, 'holdings': items,
             'missing_prices': [], 'realized': account['realized']},
            {'source': 'Google Finance 延遲報價（參考估值）',
             'date': quote_day, 'fallback': True})


def valuation_curve_rows(history, valued, info, portfolio_id, transactions=()):
    """Connect the currently displayed valuation to the chart, without backfill.

    Keep stored closes immutable. The last point carries its real source and
    cannot describe a day before any transaction used by the current account.
    """
    from copy import deepcopy
    rows = [deepcopy(r) for r in history
            if r.get('portfolio_id') == portfolio_id and r.get('date')
            and r.get('nav') not in (None, '')]
    for row in rows:
        row['kind'] = '歷史紀錄'
    day = info.get('date')
    try:
        date.fromisoformat(day)
        nav = money(valued['nav']) if valued.get('nav') is not None else None
    except (ValueError, TypeError):
        return sorted(rows, key=lambda r: r['date'])
    later_trade = any(str(t.get('trade_date') or str(t.get('at') or '')[:10]) > day
                      for t in transactions if t.get('portfolio_id') == portfolio_id)
    latest = max((r['date'] for r in rows), default='')
    if nav is not None and nav >= 0 and not valued.get('missing_prices') and day > latest and not later_trade:
        row = dict.fromkeys(['cash', 'equity_value', 'cumulative_pnl', 'liabilities',
                             'daily_pnl', 'daily_return', 'twr', 'note'], '')
        row.update({'portfolio_id': portfolio_id, 'date': day, 'nav': str(nav),
                    'cash': str(valued['cash']), 'equity_value': str(valued['market_value']),
                    'source': info['source'],
                    'kind': 'Google備援估值' if info.get('fallback') else '研究行情估值'})
        rows.append(row)
    return sorted(rows, key=lambda r: r['date'])
