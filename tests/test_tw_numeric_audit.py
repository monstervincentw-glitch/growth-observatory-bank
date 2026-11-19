"""Prevent quarterly/YTD, units and comparison-period evidence mix-ups."""
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'performance'))
from tw_numeric_audit import formulas, official_facts, quarter_end


class NumericAuditTest(unittest.TestCase):
    def setUp(self):
        self.dates=[quarter_end(2024+i//4,i%4+1) for i in range(9)]
        self.income=[{'date':d,'revenue':100,'operatingIncome':10} for d in self.dates]
        self.income[-1]['revenue']=200
        self.cash=[dict(date=d,type='CashFlowsFromOperatingActivities',value=(i%4+1)*20) for i,d in enumerate(self.dates)]

    def test_ytd_cashflow_is_differenced_at_year_boundary(self):
        values,deps,raw=formulas(self.income,self.cash,self.dates[-1])
        self.assertEqual(values['revenue_ttm'],Decimal(500))
        self.assertEqual(values['revenue_growth'],Decimal('.25'))
        self.assertEqual(values['quarterly_growth'],Decimal(1))
        self.assertEqual(values['operating_margin'],Decimal('.08'))
        self.assertEqual(values['cfo_margin'],Decimal('.16'))
        self.assertEqual(values['prior_release_growth'],Decimal(0))
        self.assertEqual(values['prior_release_margin'],Decimal('.1'))

    def test_previous_growth_needs_nine_distinct_quarters(self):
        with self.assertRaisesRegex(ValueError,'nine revenue quarters'):
            formulas(self.income[:-1],self.cash,self.dates[-2])

    def test_conflicting_vendor_cashflow_alias_is_refused(self):
        self.cash.append(dict(date=self.dates[-1],type='NetCashInflowFromOperatingActivities',value=21))
        with self.assertRaisesRegex(ValueError,'aliases'):formulas(self.income,self.cash,self.dates[-1])

    def test_official_annual_less_q3_is_q4_and_units_stay_exact(self):
        transcription={'unit':'NTD_THOUSANDS','symbol':'2330','records':[
            {'document':'FY.pdf','incomePage':5,'cashflowPage':8,'quarters':{},
             'ytd':{'2025-12-31':{'revenue':100,'operatingCashflow':30}}},
            {'document':'Q3.pdf','incomePage':5,'cashflowPage':8,'quarters':{},
             'ytd':{'2025-09-30':{'revenue':70,'operatingCashflow':20}}}]}
        documents=[{'pdf':name,'url':'https://investor.tsmc.com/'+name,'symbol':'2330',
                    'sha256':'a'*64,'retrievedAt':'2026-11-19T00:00:00Z'} for name in ['FY.pdf','Q3.pdf']]
        facts=official_facts(transcription,documents)
        self.assertEqual(facts['2330','2025-12-31','revenue']['value'],'30000')
        self.assertEqual(facts['2330','2025-12-31','operatingCashflow']['value'],'30000')
        self.assertEqual(len(facts['2330','2025-12-31','revenue']['operands']),2)
        # One dollar difference is preserved; raw values have no tolerance.
        transcription['records'][0]['ytd']['2025-12-31']['revenue']=100.001
        facts=official_facts(transcription,documents)
        self.assertEqual(Decimal(facts['2330','2025-12-31','revenue']['value']),Decimal(30001))

    def test_conflicting_official_reports_cannot_overwrite_each_other(self):
        row={'document':'Q.pdf','incomePage':5,'cashflowPage':8,
             'quarters':{'2025-06-30':{'revenue':10}},'ytd':{}}
        other=deepcopy(row);other['quarters']['2025-06-30']['revenue']=11
        doc={'pdf':'Q.pdf','url':'https://investor.tsmc.com/Q.pdf','symbol':'2330',
             'sha256':'a'*64,'retrievedAt':'2026-11-19T00:00:00Z'}
        with self.assertRaisesRegex(ValueError,'Conflicting official'):
            official_facts({'symbol':'2330','unit':'NTD_THOUSANDS','records':[row,other]},[doc])


if __name__=='__main__':unittest.main()
