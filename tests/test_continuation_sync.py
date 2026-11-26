import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'performance'))
from continuation_sync import eligible_version
from primary_valuation import history_for
class ContinuationTests(unittest.TestCase):
 def test_late_financial_observation_does_not_enter_prior_close(self):
  a=dict(period='2026-06-30',eligibleFrom='2026-11-10T01:00:00Z',firstVerifiedAt='2026-11-10T01:00:00Z')
  b=dict(period='2026-09-30',eligibleFrom='2026-11-23T07:00:00Z',firstVerifiedAt='2026-11-23T07:00:00Z')
  self.assertEqual(eligible_version([a,b],'2026-11-23','TW'),a)
  self.assertEqual(eligible_version([a,b],'2026-11-24','TW'),b)
 def test_no_eligible_version_is_not_filled_from_future(self):
  self.assertIsNone(eligible_version([dict(period='2026-09-30',eligibleFrom='2026-11-25T00:00:00Z')],'2026-11-24','TW'))
 def test_old_quote_archive_cannot_replace_newer_published_curve(self):
  block={'stocks':[dict(symbol='A',integrity=True,priceDate='2026-11-24',priceValidation={'cutoff':'2026-11-24'},history=[dict(date='2026-11-04',close=10),dict(date='2026-11-24',close=12)])]}
  old=dict(day='2026-11-19',quotes={'A':dict(priceDate='2026-11-19',priceValidation={'cutoff':'2026-11-19'},bars=[dict(date='2026-11-04',adjClose=10),dict(date='2026-11-19',adjClose=11)])})
  self.assertIn('2026-11-24',history_for(block,old,'2026-11-04','2026-11-24')['A'])

 def test_us_new_report_waits_two_days_and_keeps_prior_report(self):
  old=dict(period='2026-07-31',eligibleFrom='2026-11-12T23:00:00Z',firstVerifiedAt='2026-11-12T23:00:00Z',financial={'max_input_filed':'2026-08-27'})
  new=dict(period='2026-10-31',eligibleFrom='2026-11-24T22:00:00Z',firstVerifiedAt='2026-11-24T22:00:00Z',financial={'max_input_filed':'2026-11-24'})
  self.assertEqual(eligible_version([old,new],'2026-11-25','US'),old)
  self.assertEqual(eligible_version([old,new],'2026-11-26','US'),new)
 def test_one_market_gap_does_not_block_other_market_or_etf_binding(self):
  import json,tempfile
  from unittest.mock import patch
  from continuation_sync import build
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'etf-inputs').mkdir();(root/'etf-inputs/2026-11-25.json').write_text('{"batch":"old"}')
   entry={'batch':'new','snapshot':{'markets':{'US':{'macro':{'marketAsOf':'2026-11-25'}},'TW':{'macro':{'marketAsOf':'2026-11-26'}}}}}
   with patch('continuation_sync.build_market',side_effect=[ValueError('US source unavailable'),None]) as markets:
    with self.assertRaises(ValueError):build(root,root,[],entry,{})
    self.assertEqual([call.args[4] for call in markets.call_args_list],['US','TW'])
   self.assertEqual(json.loads((root/'etf-inputs/2026-11-25.json').read_text())['batch'],'new')
