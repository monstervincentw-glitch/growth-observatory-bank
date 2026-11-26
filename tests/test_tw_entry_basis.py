import sys,unittest,copy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'portfolio_portal'))
from tw_entry_basis import resolve,OBSERVED,buy_cutoff
from entry_basis import apply_official_baselines,missing_baseline_rows

def registry(at='2026-11-12T14:00:00Z',growth=.3,margin=.2):
 return {'2330':{'versions':{'v1':dict(id='v1',source='FinMind',period='2026-09-30',firstVerifiedAt=at,eligibleFrom=at,financial=dict(financial_status='ok',latest_period='2026-09-30',revenue_growth=growth,operating_margin=margin))}}}
class TaiwanBaselineTests(unittest.TestCase):
 def test_pre_buy_observation_is_not_called_official(self):
  r=resolve('2330','2026-11-25',registry(),'2026-11-25T06:00:00Z')
  self.assertEqual(r['verification'],OBSERVED);self.assertEqual(r['official_published_at'],'');self.assertEqual(r['revenue_growth'],.3)
 def test_same_day_after_buy_observation_is_rejected(self):
  self.assertFalse(resolve('2330','2026-11-25',registry('2026-11-25T06:01:00Z'),'2026-11-25T06:00:00Z')['ok'])
 def test_historical_date_uses_conservative_open_instead_of_late_record_time(self):
  self.assertFalse(resolve('2330','2026-11-24',registry('2026-11-24T02:00:00Z'),'2026-11-25T06:00:00Z')['ok'])
 def test_missing_invalid_and_future_financials_stay_unknown(self):
  self.assertFalse(resolve('0000','2026-11-25',registry())['ok'])
  self.assertFalse(resolve('2330','2026-11-25',registry(growth=float('nan')))['ok'])
 def test_zero_is_a_valid_entry_and_first_verified_baseline_is_fixed(self):
  tx=dict(id='t',portfolio_id='p',action='BUY',market='TW',symbol='2330',trade_date='2026-11-25',at='2026-11-25T06:00:00Z')
  original=copy.deepcopy(tx)
  a=resolve('2330',tx['trade_date'],registry(growth=0,margin=0),tx['at'])|dict(portfolio_id='p',ledger_id='t')
  b=resolve('2330',tx['trade_date'],registry(growth=.9,margin=.8),tx['at'])|dict(portfolio_id='p',ledger_id='t')
  self.assertEqual(apply_official_baselines([tx],[a,b])[0]['entry_growth'],0)
  self.assertEqual(tx,original)
  self.assertEqual(missing_baseline_rows([tx],[a],[],lambda _:self.fail('SEC called'),tw_registry=registry()),[])
if __name__=='__main__':unittest.main()
