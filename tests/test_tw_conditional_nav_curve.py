"""Tests for a conditional mark-to-market curve that cannot alter official trades."""
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from performance.conditional_curve import build_conditional_curve


class ConditionalCurveTest(unittest.TestCase):
    def setUp(self):
        self.temporary=TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path=Path(self.temporary.name)
        (self.path/'sessions').mkdir()
        self.baseline={'date':'2026-11-04','TW':[
            {'year':year,'variant':variant,'capital':3_000_000,
             'nav':5_000_000,'cash':500_000,
             'positions':[{'symbol':'2330','value':2_000_000},{'symbol':'2454','value':2_500_000}]}
            for year in range(2016,2027) for variant in ('A','B')
        ]}

    def write_day(self,day,price_factor=1.0,mutator=None):
        rows={}
        for year in range(2016,2027):
            values=[]
            for variant in 'AB':
                positions=[
                    {'symbol':'2330','value':2_000_000,'markedValue':2_000_000*price_factor},
                    {'symbol':'2454','value':2_500_000,'markedValue':2_500_000*price_factor},
                ]
                values.append({'variant':variant,'cash':500_000,
                    'value':500_000+4_500_000*price_factor,'positions':positions})
            rows[str(year)]=values
        item={'schema':1,'market':'TW','status':'CONDITIONAL_FIXED_HOLDINGS',
              'anchor':'2026-11-04','through':day,'priceHash':'a'*64,'years':rows}
        if mutator:mutator(item)
        (self.path/'sessions'/f'{day}.json').write_text(json.dumps(item))
        (self.path/'latest.json').write_text(json.dumps(item))

    def test_appends_exact_daily_marks_without_formal_trades(self):
        dates=['2026-11-05','2026-11-06','2026-11-09']
        for n,d in enumerate(dates):self.write_day(d,1-(n+1)/100)
        out=build_conditional_curve(self.path,self.baseline,'TW',expected_dates=dates)
        self.assertEqual(out['through'],'2026-11-09')
        self.assertEqual(out['accountCount'],22)
        self.assertFalse(out['tradingReplayVerified'])
        self.assertTrue(out['noPostAnchorTrading'])
        self.assertEqual(len(out['curves']['TW_2026_A']),4)
        self.assertEqual(out['curves']['TW_2026_A'][0],['2026-11-04',5/3])
        self.assertAlmostEqual(out['latest']['TW_2026_A']['value'],4_865_000)
        self.assertAlmostEqual(out['latest']['TW_2026_A']['changeSinceSeal'],-.027)
        self.assertEqual(out['curves']['TW_2026_A'],out['curves']['TW_2026_B'])
        self.assertTrue((self.path/'curve.json').is_file())

    def test_missing_session_refused_without_replacing_curve(self):
        self.write_day('2026-11-05')
        first=build_conditional_curve(self.path,self.baseline,'TW')
        previous=(self.path/'curve.json').read_text()
        self.write_day('2026-11-09')
        with self.assertRaisesRegex(ValueError,'missing required'):
            build_conditional_curve(self.path,self.baseline,'TW',
                                    expected_dates=['2026-11-05','2026-11-06','2026-11-09'])
        self.assertEqual((self.path/'curve.json').read_text(),previous)

    def test_changed_positions_or_incorrect_nav_refused(self):
        self.write_day('2026-11-05',mutator=lambda x:x['years']['2026'][0]['positions'][0].update(symbol='9999'))
        with self.assertRaisesRegex(ValueError,'positions changed'):
            build_conditional_curve(self.path,self.baseline,'TW')
        self.write_day('2026-11-05',mutator=lambda x:x['years']['2026'][0].update(value=1))
        with self.assertRaisesRegex(ValueError,'fails cash/position'):
            build_conditional_curve(self.path,self.baseline,'TW')

    def test_changed_cash_refused(self):
        self.write_day('2026-11-05',mutator=lambda x:x['years']['2026'][0].update(cash=1))
        with self.assertRaisesRegex(ValueError,'cash changed'):
            build_conditional_curve(self.path,self.baseline,'TW')


if __name__=='__main__':
    unittest.main()
