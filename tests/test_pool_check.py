import copy
import gzip
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('pool_check', ROOT / 'portfolio_portal/pool_check.py')
pool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pool)
STAMP = '2026-11-21T00:00:00+00:00'


class PoolCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (ROOT / 'data/snapshot.json.gz').read_bytes()
        cls.source = pool.decode_source(cls.raw)

    def test_check_keeps_all_engine_inputs_and_is_idempotent(self):
        before = copy.deepcopy(self.source)
        after = pool.audit(before, STAMP, lambda: (self.source, 'a' * 64))
        for market in pool.RULES:
            self.assertEqual(before['markets'][market]['stocks'], after['markets'][market]['stocks'])
            self.assertEqual(before['markets'][market]['marks'], after['markets'][market]['marks'])
            record = after['markets'][market]['macro']['poolCheck']
            self.assertEqual(record['status'], 'MATCHED_BASELINE')
            self.assertFalse(record['automaticMembershipEnabled'])
        self.assertEqual(before, self.source)
        self.assertIsNone(pool.audit(after, '2026-11-21T08:00:00+00:00', lambda: self.fail('duplicate request')))

    def test_failure_preserves_last_complete_comparison_never_removes_pool(self):
        after = pool.audit(self.source, STAMP, lambda: (self.source, 'a' * 64))
        failed = pool.audit(after, '2026-11-22T00:00:00+00:00', lambda: (_ for _ in ()).throw(TimeoutError()))
        for market in pool.RULES:
            record = failed['markets'][market]['macro']['poolCheck']
            self.assertEqual(record['status'], 'SOURCE_FAILED')
            self.assertEqual(record['verifiedAt'], STAMP)
            self.assertEqual(record['sourceHash'], 'a' * 64)
            self.assertEqual(failed['markets'][market]['stocks'], after['markets'][market]['stocks'])
        self.assertIsNone(pool.audit(failed, '2026-11-22T00:10:00+00:00', lambda: self.fail('early retry')))

    def test_drift_is_pending_without_invented_execution_or_effective_date(self):
        changed = copy.deepcopy(self.source)
        for market in pool.RULES:
            changed['markets'][market]['stocks'].pop()
        after = pool.audit(self.source, STAMP, lambda: (changed, 'b' * 64))
        for market in pool.RULES:
            check = after['markets'][market]['macro']['poolCheck']
            self.assertEqual(check['status'], 'PENDING_RULES')
            self.assertEqual(len(check['changes']), 1)
            self.assertIsNone(check['changes'][0]['effectiveDate'])
            self.assertFalse(check['changes'][0]['applied'])
            self.assertEqual(after['markets'][market]['stocks'], self.source['markets'][market]['stocks'])

    def test_partial_or_duplicate_source_rejects_both_markets(self):
        for mode in ('missing_market', 'empty', 'duplicate', 'metadata'):
            bad = copy.deepcopy(self.source)
            if mode == 'missing_market':
                del bad['markets']['TW']
            elif mode == 'empty':
                bad['markets']['TW']['stocks'] = []
            elif mode == 'duplicate':
                bad['markets']['TW']['stocks'].append(bad['markets']['TW']['stocks'][0])
            else:
                del bad['markets']['TW']['stocks'][0]['ticker']
            after = pool.audit(self.source, STAMP, lambda: (bad, 'x'))
            self.assertTrue(all(after['markets'][m]['macro']['poolCheck']['status'] == 'SOURCE_FAILED' for m in pool.RULES))

    def test_retry_budget_survives_reloading_saved_envelope(self):
        current = self.source
        for minute in ('00:00', '00:20', '00:40'):
            current = pool.audit(current, f'2026-11-21T{minute}:00+00:00', lambda: (_ for _ in ()).throw(TimeoutError()))
            current = json.loads(json.dumps(current))
        self.assertEqual(current['markets']['US']['macro']['poolCheck']['attempts'], 3)
        self.assertIsNone(pool.audit(current, '2026-11-21T01:00:00+00:00', lambda: self.fail('over budget')))

    def test_truncated_source_never_becomes_empty_list(self):
        for bad in (self.raw[:80], gzip.compress(b'{}')):
            with self.assertRaises((ValueError, EOFError, KeyError)):
                pool.decode_source(bad)


if __name__ == '__main__':
    unittest.main()
