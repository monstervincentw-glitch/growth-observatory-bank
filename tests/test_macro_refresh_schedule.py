import sys
from pathlib import Path
from datetime import datetime
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'portfolio_portal'))
import pipeline_worker as worker


class MacroRefreshSchedule(unittest.TestCase):
    def test_release_changes_refresh_target_without_new_quote_day(self):
        macro = {'marketAsOf': '2026-11-20', 'macro_vintage_date': '2026-11-19',
                 'liveMacroPolicy': worker.LIVE_MACRO_POLICY}
        self.assertFalse(worker.macro_refresh_required(macro, datetime.fromisoformat('2026-11-20T21:59:59+00:00')))
        self.assertTrue(worker.macro_refresh_required(macro, datetime.fromisoformat('2026-11-20T22:00:00+00:00')))
        macro['macro_vintage_date'] = '2026-11-20'
        self.assertFalse(worker.macro_refresh_required(macro, datetime.fromisoformat('2026-11-23T22:00:00+00:00')))

    def test_completed_batch_does_not_fetch_sources_or_history(self):
        macro = {'marketAsOf': '2026-11-20', 'macro_vintage_date': '2026-11-20',
                 'liveMacroPolicy': worker.LIVE_MACRO_POLICY}
        snapshot = {'markets': {'US': {'macro': macro, 'stocks': [
            {'financial': {'date_basis': 'LIVE_OBSERVED_SEC'}}]}}}
        batch = {'day': '2026-11-20'}
        import json
        book = unittest.mock.Mock()
        book.values_get.side_effect = [
            {'values': []}, {'values': [['header'], ['US', '', '', '', '', '', '', '', json.dumps(batch)]]},
            {'values': []}]
        client = unittest.mock.Mock()
        client.open_by_key.return_value = book
        with patch('research.decode_envelope', return_value={'snapshot': snapshot}), \
             patch('pool_check.audit', return_value=None), \
             patch.object(worker, 'get_json') as source, \
             patch.object(worker, 'latest_finance') as finance:
            worker.run_cycle(client, 'node', {})
        source.assert_not_called()
        finance.assert_not_called()
        self.assertEqual(book.values_get.call_count, 3)


if __name__ == '__main__':
    unittest.main()
