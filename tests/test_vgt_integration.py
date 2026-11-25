"""The added ETF must resume normally without rewriting existing accounts."""
from copy import deepcopy
import csv, json, shutil, sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'performance'))
from live_etf import advance,read_state,signals_for
from performance_view import overlay
from publication import decode,validate_view
from vgt_exports import export_vgt

DATA=ROOT.parent/'published_vgt'
INPUTS=ROOT.parent/'publish_research'

class VGTIntegrationTest(unittest.TestCase):
    def test_seed_contains_all_new_cohorts_and_displays_new_product_once(self):
        _,view,states=read_state(ROOT/'performance/seed')
        self.assertEqual(len(states),1060)
        rows=[r for r in view['summary'] if r['symbol']=='VGT']
        self.assertEqual(len(rows),84)
        old={'advisor':[view['summary'][0]],'windows':[]}
        updated=overlay(old,view)
        self.assertEqual(len(updated['advisor']),1060)
        self.assertEqual(len(overlay(updated,view)['advisor']),1060)
        self.assertAlmostEqual(next(r for r in rows if r['start_year']==2006 and r['variant']=='B')['cumulative'],45.24127124994948)

    def test_missing_vgt_cohort_is_rejected_but_complete_legacy_release_remains_readable(self):
        _,view,_=read_state(ROOT/'performance/seed')
        legacy=deepcopy(view);legacy['summary']=[r for r in legacy['summary'] if r['symbol']!='VGT']
        validate_view(legacy)
        incomplete=deepcopy(view);incomplete['summary'].pop()
        with self.assertRaises(ValueError):validate_view(incomplete)

    @unittest.skipUnless(DATA.exists() and INPUTS.exists(),'live fixture only required during migration')
    def test_next_session_red_exits_and_exports_are_idempotent(self):
        old_manifest,old_view,old_states=read_state(DATA/'etf')
        feed=json.loads((INPUTS/'etf-inputs/2026-11-24.json').read_text())
        additions=json.loads((DATA/'etf/price-additions.json').read_text())
        feed['prices'].update(additions['prices'])
        feed.update(cutoff='2026-11-25',batch='synthetic-vgt-regression')
        for bars in feed['prices'].values():
            last=bars[-1]
            bars.append(last|{'date':'2026-11-25',**{k:last[k]*1.01 for k in ('open','close','adjOpen','adjClose')}})
        signals,evidence=signals_for(INPUTS,pd.Timestamp('2026-11-24'))
        signals.loc[pd.Timestamp('2026-11-25')]=[2,pd.Timestamp('2026-11-23')]
        with TemporaryDirectory() as temporary:
            out=Path(temporary)
            shutil.copytree(DATA/'etf',out/'etf')
            shutil.copytree(DATA/'etf-ledger',out/'etf-ledger')
            with patch('live_etf.signals_for',return_value=(signals,evidence)):
                updated=advance(INPUTS,feed,out/'etf',out/'next-etf')
            self.assertEqual(updated['through'],'2026-11-25')
            _,_,state=read_state(out/'next-etf')
            self.assertAlmostEqual(state['VGT_2006_B']['position']['target_value'],0.)
            self.assertGreater(state['VGT_2006_B']['position']['cash'],0.)
            self.assertAlmostEqual(state['VGT_2006_B']['position']['shy_value'],0.)
            self.assertGreater(state['VGT_2006_D']['position']['shy_units'],0.)
            self.assertEqual(state['VGT_2006_B']['state']['sell_count'],old_states['VGT_2006_B']['state']['sell_count']+1)
            ledger=json.loads((out/'next-etf/ledger.json').read_text())
            self.assertEqual(len([d for d in ledger['daily'] if d['symbol']=='VGT']),84)
            (out/'etf-ledger/2026-11-25.json').write_text(json.dumps(ledger))
            for p in (out/'next-etf').iterdir():
                if p.name!='ledger.json':p.replace(out/'etf'/p.name)
            export_vgt(out)
            once={name:(out/'etf'/name).read_bytes() for name in ['vgt-performance.csv','vgt-nav-2006.csv','vgt-trades.csv']}
            export_vgt(out)
            self.assertEqual(once,{name:(out/'etf'/name).read_bytes() for name in once})
            with (out/'etf/vgt-performance.csv').open() as h:rows=list(csv.reader(h))
            self.assertEqual(len(rows),85)
            self.assertTrue(all(row[3]=='2026-11-25' for row in rows[1:]))
            self.assertIn('2026-11-25',once['vgt-nav-2006.csv'].decode())
        # The production fixture is read-only throughout the synthetic replay.
        self.assertEqual(read_state(DATA/'etf')[2],old_states)

if __name__=='__main__':unittest.main()
