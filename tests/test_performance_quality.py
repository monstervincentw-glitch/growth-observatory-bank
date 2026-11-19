"""Price-vintage conflicts and partial subsystem failures must stay visible."""
from copy import deepcopy
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

import pandas as pd

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'performance'))
from etf_vintages import reconcile_anchor
from quality import audit_tw, check_scope, qualify_tw
from primary_live import sealed_engine


class ETFVintageTest(unittest.TestCase):
    def setUp(self):
        self.anchor=pd.Timestamp('2026-11-13')
        self.old=dict(date='2026-11-13',open=90.014,close=90.04,adjOpen=90.014,adjClose=90.04)
        self.new=dict(self.old,open=90.03,adjOpen=90.03)
        self.frame=pd.DataFrame([self.new,self.old|{'date':'2026-11-16','open':90.2,'adjOpen':90.2}]).set_index('date')
        self.frame.index=pd.DatetimeIndex(self.frame.index)
        self.adjusted=self.frame[['adjOpen','adjClose']].rename(columns={'adjOpen':'tr_open','adjClose':'tr_close'})
        self.sealed={'prices':{'IEF':[self.old]}}
        self.ledger={'trades':[]}

    def reconcile(self):
        return reconcile_anchor('IEF',self.frame,self.adjusted,{'tr_open':90.014,'tr_close':90.04},
                                self.anchor,{'2026-11-13':90.04},self.sealed,self.ledger)

    def test_sealed_and_new_vintages_remain_intact_and_strictly_separated(self):
        original=self.frame.copy(); sealed=deepcopy(self.sealed)
        selected,audit=self.reconcile()
        self.assertEqual(selected.loc[self.anchor,'tr_open'],90.014)
        self.assertEqual(selected.loc['2026-11-16','tr_open'],90.2)
        pd.testing.assert_frame_equal(self.frame,original);self.assertEqual(self.sealed,sealed)
        self.assertEqual(audit['laterOpen'],90.03);self.assertEqual(audit['anchorTrades'],0)

    def test_actual_historical_trade_price_revision_stays_blocked(self):
        self.ledger['trades']=[{'date':'2026-11-13','symbol':'IEF','side':'BUY'}]
        with self.assertRaisesRegex(ValueError,'sealed trades'):self.reconcile()

    def test_close_or_adjustment_basis_changes_cannot_be_waived(self):
        self.frame.loc[self.anchor,'close']=90.05
        with self.assertRaises(AssertionError):self.reconcile()
        self.setUp();self.frame.loc[self.anchor,'adjClose']=89.9
        with self.assertRaises(AssertionError):self.reconcile()

    def test_wrong_sealed_open_or_overlap_stays_blocked(self):
        self.sealed['prices']['IEF'][0]['adjOpen']=90.013
        with self.assertRaises(AssertionError):self.reconcile()


class PerformanceHealthTest(unittest.TestCase):
    def test_failed_etf_cannot_be_hidden_by_healthy_equity_results(self):
        report={'results':{'ETF':{'status':'VALIDATION_FAILED','through':'2026-11-13','requested':'2026-11-18'},
                           'US':{'status':'NO_NEW_DATA','through':'2026-11-18','requested':'2026-11-18'}}}
        with redirect_stdout(StringIO()) as messages:
            self.assertFalse(check_scope(report,'ETF'));self.assertTrue(check_scope(report,'US'))
        self.assertIn('::error::ETF',messages.getvalue())

    def test_qualification_does_not_promote_a_calculation_failure(self):
        result=qualify_tw({'status':'WAITING_FOR_CONTINUITY'},{'status':'QUALIFIED_RECONSTRUCTED_HISTORY'})
        self.assertEqual(result['status'],'WAITING_FOR_CONTINUITY')
        self.assertFalse(result['pointInTimeFullyVerified'])

    def test_report_publication_times_do_not_verify_historical_numbers(self):
        with TemporaryDirectory() as t:
            root=Path(t);inputs=root/'research';output=root/'performance'
            folder=inputs/'reconstructed-primary-inputs/TW';folder.mkdir(parents=True)
            (output/'primary/TW').mkdir(parents=True)
            (output/'primary/TW/release.json').write_text(json.dumps({'through':'2026-11-19','curves':{'TW_2026_A':[['2026-11-16',1.]]}}))
            archive=dict(stocks={str(n):{'officialPDF':{'uploadedAt':'2026-11-02T11:00:00+08:00'},
                                        'originalVendorNumericalVintageVerified':False} for n in range(80)},
                         observedAt='2026-11-17T05:00:00Z')
            (folder/'2026-11-16.json').write_text(json.dumps(archive))
            report=audit_tw(inputs,output);row=next(r for r in report['sessions'] if r['day']=='2026-11-16')
            self.assertEqual(row['officialReportTimeAvailable'],80)
            self.assertEqual(row['historicalNumericalVersionVerified'],0)
            self.assertFalse(row['pointInTimeFullyVerified']);self.assertTrue(row['calculationInPublishedCurve'])
            result=qualify_tw({'status':'VERIFIED'},report)
            self.assertEqual(result['status'],'CALCULATED_EVIDENCE_QUALIFIED')


class TaiwanExtraExecutionTest(unittest.TestCase):
    def test_original_b_can_execute_two_extras_at_one_fixed_threshold(self):
        from types import SimpleNamespace
        engine,kernel,cfg=sealed_engine('TW')
        dates=pd.date_range('2026-01-01',periods=4,freq='B')
        core=[f'C{i}' for i in range(10)];names=core+['R','E1','E2','EQ']
        fields=dict(name='fixture',financial_status='ok',financial_age_days=0,revenue_growth=.3,
            quarterly_growth=.2,operating_margin=.2,cfo_margin=.2,prior_release_growth=.3,
            prior_release_margin=.2,macro_status='vintage_verified',macro_stress=0,
            fundamental_release_key='fixture',selection_score=.6,financial_event_date=dates[0],
            macro_vintage_date=dates[0],drawdown_126=-.1,relative_63=-.01,technical_divergence=False)
        rows={d:{s:fields.copy() for s in names} for d in dates}
        rows[dates[1]]['C0']['quarterly_growth']=-.1
        rows[dates[2]]['E1']['selection_score']=.98;rows[dates[2]]['E2']['selection_score']=.97
        quotes={d:{s:dict(adj_open=100.,adj_close=100.,adj_mark=100.,observed_price=True,opening_gap=0.)
                   for s in names} for d in dates}
        quotes[dates[2]]['C0'].update(adj_open=600.,adj_mark=600.,adj_close=600.)
        eligible={d:{'primary':[]} for d in dates}
        eligible[dates[0]]['primary']=core;eligible[dates[1]]['primary']=['R']
        eligible[dates[2]]['primary']=['E1','E2','EQ']
        market=SimpleNamespace(cfg=cfg,calendar=dates,rows=rows,eligible=eligible,price=lambda s,d:quotes[d][s])
        a=kernel(market,dates[1],dates[-1],'primary',extras=False)
        b=kernel(market,dates[1],dates[-1],'primary',extras=True)
        extra=b[1].loc[b[1].reason.eq('excess_cash_high_score')]
        self.assertEqual(extra.symbol.tolist(),['E1','E2'])
        self.assertEqual(extra.core_max_score.tolist(),[.6,.6])
        self.assertEqual(int(a[0].positions.max()),10);self.assertEqual(int(b[0].positions.max()),12)
        self.assertTrue(b[0].core_positions.le(10).all());self.assertTrue(b[0].cash_twd.ge(-1e-5).all())


if __name__=='__main__':unittest.main()
