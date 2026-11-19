"""Close-time fiscal gates and original-weekly replay recovery must fail closed."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest

import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'performance'))
from tw_daily_inputs import build_input
from primary_live import replay_funding


class TaiwanDailyInputsTest(unittest.TestCase):
    def setUp(self):
        self.day='2026-11-19'
        fiscal=dict(financial_status='ok',latest_period='2026-09-30',revenue_growth=.2,
                    quarterly_growth=.1,operating_margin=.2,cfo_margin=.15,revenue_ttm=1000,
                    fundamental_release_key='2026-09-30|fixture',prior_release_growth=.15,prior_release_margin=.2)
        version=dict(id='old-source-id',source='FinMind',period='2026-09-30',financial=deepcopy(fiscal),
                     firstVerifiedAt='2026-11-17T03:00:00Z',eligibleFrom='2026-11-17T03:00:00Z')
        bars=[dict(date=d,open=10.,close=11.,adjOpen=10.,adjClose=11.,volume=100.)
              for d in ('2026-11-18','2026-11-19')]
        self.universe={str(2000+n):{} for n in range(80)}
        self.observation=dict(schema=1,market='TW',day=self.day,expected=80,status='PRICES_VERIFIED',
                              decisionAt=self.day+'T13:30:00+08:00',observedAt=self.day+'T14:00:00Z',
                              batch='archive-fixture',inactive=[],quotes={},observations={})
        for symbol in self.universe:
            self.observation['quotes'][symbol]=dict(priceDate=self.day,priceValidation={'cutoff':self.day},
                financialVersion=version['id'],financeError=None,close=11.,bars=deepcopy(bars))
            self.observation['observations'][symbol]=dict(financial=deepcopy(fiscal),financialVersion=deepcopy(version),
                features=dict(history_sessions=300,dollar_volume=4e8,drawdown_126=-.2,relative_63=-.1),
                evaluation=dict(tradable=True,divergence=True))
        self.weekly=dict(day='2026-11-13',source='ALFRED',observedAt='2026-11-14T01:00:00Z',
                         series={s:'observation_date,'+s+'_20261113\n2026-09-01,1' for s in ('CPIAUCSL','DFII10','FEDFUNDS','NFCI')})
        self.funding=dict(score=0,date=pd.Timestamp('2026-11-13'),score_range=(0,1))

    def build(self):
        return build_input(self.observation,universe=self.universe,funding=self.funding,weekly=self.weekly,
                           source_sha='a'*64,weekly_sha='b'*64,source_path='observations.json',
                           weekly_path='weekly.json',recovered_at='2026-11-19T15:00:00Z')

    def test_recover_all_80_without_using_live_cpi_overlay(self):
        self.observation['macro']={'researchEstimates':[{'availableAt':'2026-11-20'}],'decision':'red'}
        result=self.build()
        self.assertEqual(result['status'],'POINT_IN_TIME_INPUT_VERIFIED')
        self.assertEqual(len(result['stocks']),80)
        self.assertNotIn('macro',result)
        self.assertEqual(result['strategyMacro']['scoreRange'],[0,1])
        self.assertEqual(result['stocks']['2000']['financialVersion']['firstVerifiedAt'],'2026-11-17T03:00:00Z')

    def test_fiscal_observed_after_close_cannot_be_backdated(self):
        v=self.observation['observations']['2000']['financialVersion']
        v.update(firstVerifiedAt='2026-11-19T08:00:00Z',eligibleFrom='2026-11-19T08:00:00Z')
        with self.assertRaisesRegex(ValueError,'unavailable at close'):self.build()

    def test_fiscal_number_from_another_version_is_refused(self):
        self.observation['observations']['2000']['financial']['revenue_growth']=.9
        with self.assertRaisesRegex(ValueError,'numerical version'):self.build()

    def test_missing_symbol_and_quote_day_cannot_be_promoted(self):
        self.observation['quotes'].pop('2000')
        with self.assertRaisesRegex(ValueError,'universe'):self.build()
        self.setUp()
        self.observation['quotes']['2000']['bars'][-1]['date']='2026-11-20'
        with self.assertRaisesRegex(ValueError,'forward-dated'):self.build()

    def test_original_weekly_vintage_cannot_arrive_after_close(self):
        self.weekly['observedAt']='2026-11-19T08:00:00Z'
        with self.assertRaisesRegex(ValueError,'not available'):self.build()

    def test_original_threshold_interval_must_give_one_decision_at_both_gates(self):
        cfg={'entry_max_macro_stress':1,'exit_macro_stress':2}
        base=pd.Series(dict(grouped_status='unknown',macro_status='vintage_verified',rate_flag=False,
                            conditions_flag=False,inflation_flag=float('nan')),name=pd.Timestamp('2026-11-13'))
        self.assertEqual(replay_funding(base,cfg)['score_range'],(0,1))
        base['rate_flag']=True
        self.assertIsNone(replay_funding(base,cfg))



if __name__=='__main__':unittest.main()
