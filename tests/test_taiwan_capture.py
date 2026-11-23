"""Late exchange publication must not exhaust all retries before the evening."""
from datetime import datetime
from pathlib import Path
import sys
import unittest
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portfolio_portal'))
from taiwan_capture import merge_quotes, slot_at, next_slot, recover_quotes

DAY = '2026-11-23'
STOCKS = [{'symbol': '2330', 'ticker': '2330.TW'}, {'symbol': '3529', 'ticker': '3529.TWO'}]
LIVE = [{'market': 'TW', 'symbol': '2330', 'price': 2030, 'quote_time': DAY + ' 13:30:00', 'currency': 'TWD', 'status': 'OK'}]
def clock(hour, minute=0):
    return datetime(2026, 11, 23, hour, minute, tzinfo=ZoneInfo('Asia/Taipei'))


class CaptureTests(unittest.TestCase):
    def test_four_slots_leave_evening_attempt_available(self):
        self.assertIsNone(slot_at(clock(14, 49)))
        self.assertIsNone(slot_at(clock(16, 29)))
        self.assertEqual(slot_at(clock(16, 30)), '16:30')
        self.assertEqual(slot_at(clock(17, 30)), '17:30')
        self.assertEqual(slot_at(clock(18, 30)), '18:30')
        self.assertEqual(slot_at(clock(21, 27)), '21:27')
        self.assertEqual(next_slot(clock(18, 45)), clock(21, 27).timestamp())
        self.assertEqual(next_slot(clock(21, 27)), 0)
        self.assertIsNone(slot_at(clock(21).replace(day=22)))

    def test_partial_prices_survive_until_otc_becomes_available(self):
        partial = merge_quotes(STOCKS, LIVE, None, [], DAY, 'first')
        self.assertEqual(partial['succeeded'], 1)
        self.assertEqual(partial['missing'], ['3529'])
        official = [{'SecuritiesCompanyCode': '3529', 'Date': '1151123', 'Close': '1,895.00'}]
        complete = merge_quotes(STOCKS, [], partial, official, DAY, 'later')
        self.assertEqual(complete['succeeded'], 2)
        self.assertEqual(complete['status'], 'REFERENCE_QUOTES_COMPLETE')
        self.assertEqual(complete['quotes']['2330']['observedAt'], 'first')
        self.assertEqual(complete['quotes']['3529']['close'], 1895)

    def test_old_dates_duplicate_rows_and_invalid_prices_do_not_complete(self):
        for official in ([{'SecuritiesCompanyCode': '3529', 'Date': '1151120', 'Close': '1895'}],
                         [{'SecuritiesCompanyCode': '3529', 'Date': '1151123', 'Close': 'nan'}],
                         [{'SecuritiesCompanyCode': '3529', 'Date': '1151123', 'Close': '1895'}] * 2):
            self.assertEqual(merge_quotes(STOCKS, LIVE, None, official, DAY, 'at')['missing'], ['3529'])
        old = merge_quotes(STOCKS, LIVE, None, [], DAY, 'at')
        self.assertEqual(merge_quotes(STOCKS, [], old, [], '2026-11-24', 'at')['succeeded'], 0)

    def test_completed_session_performs_no_reads_or_source_requests(self):
        def fail(*args):
            raise AssertionError('Completed day performed work')
        class Book:
            values_get = fail
        published = {'day': DAY}
        result = recover_quotes(Book(), {'macro': {'marketAsOf': DAY}, 'stocks': STOCKS},
                                published, fail, fail, fail, clock(21, 27))
        self.assertIs(result, published)

    def test_failed_slot_is_persisted_and_not_repeated_after_restart(self):
        class Book:
            def __init__(self):
                self.state = []
            def values_get(self, rectangle):
                if rectangle.startswith('quote_capture_state'):
                    return {'values': [['header']] + self.state}
                raise ValueError('Google temporary failure')
        book = Book()
        def save(book, rectangle, rows):
            book.state = rows
        requests = []
        def source(url):
            requests.append(url)
            return []
        block = {'macro': {'marketAsOf': '2026-11-20'}, 'stocks': STOCKS}
        published = {'day': '2026-11-20'}
        recover_quotes(book, block, published, source, lambda *a: None, save, clock(16, 30))
        self.assertEqual(book.state[0][3], 'FAILED')
        saved = list(book.state[0])
        recover_quotes(book, block, published, source, lambda *a: None, save, clock(16, 55))
        self.assertEqual(book.state[0], saved)
        recover_quotes(book, block, published, source, lambda *a: None, save, clock(21, 27))
        self.assertEqual(book.state[0][2], '21:27')

    def test_exchange_failure_preserves_prices_before_the_network_request(self):
        import json
        from taiwan_capture import HEADERS
        class Book:
            def __init__(self):
                self.state = []
            def values_get(self, rectangle):
                if rectangle.startswith('quote_capture_state'):
                    return {'values': [HEADERS] + self.state}
                if rectangle.startswith('market_quotes'):
                    h = ['market', 'symbol', 'google_ticker', 'price', 'quote_time', 'currency', 'status', 'exchange']
                    return {'values': [h] + [[r.get(k, '') for k in h] for r in LIVE]}
                raise AssertionError(rectangle)
        book = Book()
        def save(book, rectangle, rows):
            book.state = rows
        def fail(url):
            # Simulate the exchange being unavailable after Google prices arrive.
            self.assertEqual(json.loads(book.state[0][8])['succeeded'], 1)
            raise ValueError('Exchange temporarily unavailable')
        block = {'macro': {'marketAsOf': '2026-11-20'}, 'stocks': STOCKS}
        recover_quotes(book, block, {'day': '2026-11-20'}, fail, lambda *a: None, save, clock(16, 30))
        saved = json.loads(book.state[0][8])
        self.assertEqual(saved['quotes']['2330']['close'], 2030)
        self.assertEqual(book.state[0][3], 'FAILED')


if __name__ == '__main__':
    unittest.main()
