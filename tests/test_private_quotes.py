from copy import deepcopy
from datetime import date
from decimal import Decimal
from quotes import reference_valuation


def fixture():
    lot = {'quantity': Decimal('2'), 'unit_cost': Decimal('50')}
    account = {'lots': {'MU': [lot]}, 'cash': Decimal('10'), 'realized': Decimal('0')}
    block = {'macro': {'marketAsOf': '2026-11-18'}, 'stocks': [
        {'symbol': 'MU', 'integrity': True, 'priceDate': '2026-11-18', 'close': 60}]}
    row = {'symbol': 'MU', 'status': 'OK', 'price': '70',
           'currency': 'USD', 'quote_time': '2026-11-19 16:00:01'}
    return account, block, row


def test_newer_marks_display_only():
    a, b, r = fixture()
    old = deepcopy(b)
    v, info = reference_valuation(a, b, [r], 'US', date(2026, 11, 20))
    assert v['nav'] == Decimal('150') and v['holdings'][0]['pnl'] == 40
    assert info['fallback'] and info['date'] == '2026-11-19'
    assert b == old and v['holdings'][0]['stock']['priceDate'] == '2026-11-18'


def test_invalid_or_stale_marks_preserve_research():
    for changes in ({'price': 'NaN'}, {'price': '-1'}, {'price': ''},
                    {'currency': 'TWD'}, {'status': 'PENDING'},
                    {'quote_time': '2026-11-21 16:00:01'},
                    {'quote_time': '2026-11-18 16:00:01'},
                    {'quote_time': '2026-11-10 16:00:01'}, {'quote_time': 'bad'}):
        a, b, r = fixture()
        r.update(changes)
        v, info = reference_valuation(a, b, [r], 'US', date(2026, 11, 20))
        assert not info['fallback'] and v['nav'] == 130


def test_partial_duplicate_and_mixed_dates_rejected():
    a, b, r = fixture()
    assert not reference_valuation(a, b, [], 'US')[1]['fallback']
    assert not reference_valuation(a, b, [r, r], 'US')[1]['fallback']
    a['lots']['NVDA'] = a['lots']['MU']
    other = dict(r, symbol='NVDA', quote_time='2026-11-20 16:00:01')
    assert not reference_valuation(a, b, [r, other], 'US', date(2026,11,20))[1]['fallback']


def test_no_research_still_valued_without_financial_signal():
    a, b, r = fixture()
    b = {'stocks': [], 'macro': {'marketAsOf': '1970-01-01'}}
    v, info = reference_valuation(a, b, [r], 'US', date(2026,11,20))
    assert info['fallback'] and v['nav'] == 150
    assert v['holdings'][0]['stock']['integrity'] is False


def test_other_market_never_uses_us_quotes():
    a, b, r = fixture()
    assert not reference_valuation(a, b, [r], 'TW', date(2026,11,20))[1]['fallback']


def test_corporate_action_keeps_research_guard():
    a, b, r = fixture()
    b['stocks'][0]['marketEvent'] = {'type': 'split_pending'}
    assert not reference_valuation(a, b, [r], 'US', date(2026,11,20))[1]['fallback']


def test_quote_reader_keeps_existing_rows_and_only_adds_missing():
    import threading
    from storage import PortfolioSheets
    from quotes import QUOTE_HEADERS
    class Book:
        def __init__(self):
            self.writes = []
        def values_get(self, rectangle):
            assert rectangle == "'live_quotes'!A1:F200"
            return {'values': [QUOTE_HEADERS, ['MU','NASDAQ:MU','70','2026-11-19 16:00:01','USD','OK']]}
        def values_append(self, rectangle, params, body):
            self.writes.append((rectangle, params, body))
    store = object.__new__(PortfolioSheets)
    store._lock = threading.RLock()
    store.book = Book()
    assert len(store.live_quotes(['MU'])) == 1
    assert store.book.writes == []
    store.live_quotes(['MU', 'ABC'])
    row = store.book.writes[0][2]['values'][0]
    assert row[:2] == ['ABC', 'ABC']
    assert 'B3' in row[2] and 'C3' in row[5]
    assert store.book.writes[0][1]['valueInputOption'] == 'USER_ENTERED'


def test_quote_schema_mismatch_and_formula_tickers_fail_without_writes():
    import threading
    import pytest
    from storage import PortfolioSheets
    class Book:
        def values_get(self, rectangle):
            return {'values': [['wrong']]}
        def values_append(self, *args, **kwargs):
            raise AssertionError('Must not write')
    store = object.__new__(PortfolioSheets)
    store._lock = threading.RLock()
    store.book = Book()
    with pytest.raises(ValueError):
        store.live_quotes(['MU'])
    with pytest.raises(ValueError):
        store.live_quotes(['=IMPORTXML("bad")'])


def test_curve_connects_latest_mark_without_mutating_history():
    from quotes import valuation_curve_rows
    history = [{'portfolio_id':'p','date':'2026-11-18','nav':'17573.80','source':'saved'}]
    v = {'nav':Decimal('17335.18'),'cash':Decimal('874.41'),
         'market_value':Decimal('16460.77'),'missing_prices':[]}
    info = {'date':'2026-11-19','fallback':True,'source':'Google Finance'}
    rows = valuation_curve_rows(history,v,info,'p')
    assert [(r['date'],r['nav']) for r in rows] == [
        ('2026-11-18','17573.80'),('2026-11-19','17335.18')]
    assert rows[-1]['kind'] == 'Google備援估值' and rows[-1]['source'] == 'Google Finance'
    assert len(history) == 1 and 'kind' not in history[0]


def test_curve_never_overwrites_saved_date_or_adds_incomplete_point():
    from quotes import valuation_curve_rows
    h = [{'portfolio_id':'p','date':'2026-11-19','nav':'100','source':'saved'}]
    v = {'nav':200,'cash':0,'market_value':200,'missing_prices':[]}
    info = {'date':'2026-11-19','fallback':True,'source':'Google Finance'}
    assert valuation_curve_rows(h,v,info,'p')[0]['nav'] == '100'
    info['date'] = '2026-11-20'
    v['nav'] = None
    assert len(valuation_curve_rows(h,v,info,'p')) == 1


def test_curve_respects_later_trades_and_portfolio_isolation():
    from quotes import valuation_curve_rows
    h = [{'portfolio_id':'other','date':'2026-11-19','nav':'100','source':'saved'}]
    v = {'nav':200,'cash':0,'market_value':200,'missing_prices':[]}
    info = {'date':'2026-11-19','fallback':True,'source':'Google Finance'}
    tx = [{'portfolio_id':'p','trade_date':'2026-11-20'}]
    assert valuation_curve_rows(h,v,info,'p',tx) == []
    assert len(valuation_curve_rows(h,v,info,'p',[])) == 1
