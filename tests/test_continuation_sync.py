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

 def test_completed_etf_same_close_batch_refresh_is_metadata_only(self):
  import json,tempfile
  from continuation_sync import bind_completed_etf_batch
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'etf').mkdir();(root/'etf-inputs').mkdir()
   (root/'etf/manifest.json').write_text('{"through":"2026-11-25"}')
   path=root/'etf-inputs/2026-11-25.json'
   original={'cutoff':'2026-11-25','status':'VERIFIED','batch':'old','prices':{'QQQ':[{'close':100}]},'weeks':['2026-11-20']}
   path.write_text(json.dumps(original))
   entry={'batch':'new','checksum':'verified-sha','snapshot':{'markets':{'US':{'macro':{'marketAsOf':'2026-11-25'}}}}}
   bind_completed_etf_batch(root,root,entry)
   data=json.loads(path.read_text());self.assertEqual(data['batch'],'new')
   self.assertEqual(data['originalResearchBatch'],'old');self.assertEqual(data['prices'],original['prices'])
   self.assertEqual(data['weeks'],original['weeks'])
   raw=path.read_bytes();bind_completed_etf_batch(root,root,entry);self.assertEqual(path.read_bytes(),raw)
   original['status']='PARTIAL';original['batch']='old';path.write_text(json.dumps(original))
   bind_completed_etf_batch(root,root,entry);self.assertEqual(json.loads(path.read_text())['batch'],'old')

 def test_same_close_main_refreshes_etf_binding_without_sources(self):
  import json,tempfile
  from unittest.mock import patch
  from continuation_sync import main
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   for market,day in [('US','2026-11-25'),('TW','2026-11-26')]:
    folder=root/'primary'/market;folder.mkdir(parents=True);(folder/'release.json').write_text(json.dumps({'through':day}))
   (root/'etf').mkdir();(root/'etf/manifest.json').write_text('{"through":"2026-11-25"}')
   (root/'etf-inputs').mkdir();feed=root/'etf-inputs/2026-11-25.json'
   feed.write_text('{"cutoff":"2026-11-25","status":"VERIFIED","batch":"old","prices":{}}')
   entry={'batch':'new','checksum':'verified-sha','snapshot':{'generatedAt':'2026-11-27T00:00:00Z','markets':{'US':{'macro':{'marketAsOf':'2026-11-25'}},'TW':{'macro':{'marketAsOf':'2026-11-26'}}}}}
   with patch('sys.argv',['continuation_sync',str(root),str(root)]),patch('continuation_sync.fetch_snapshot',return_value=([],entry)),patch('continuation_sync.subprocess.run') as sources:
    main();sources.assert_not_called()
   self.assertEqual(json.loads(feed.read_text())['batch'],'new')
