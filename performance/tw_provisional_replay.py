"""Run the original sealed Taiwan A/B engine against *retrospectively reconstructed*
daily research values. Publish ONLY under research-data/reconstructed-primary,
never as formally VERIFIED strategy performance.

Official PDF upload times and past prices are known. The 2026-11-17 FinMind
numeric values were not themselves observed at the prior 11/5-11/13 closes.
"""
import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

import primary_live

RECON_STATUS='RECONSTRUCTED_TRADES_NUMERIC_VINTAGE_PENDING'

def load_reconstructed(inputs,market,days):
    if market!='TW':
        raise ValueError('Taiwan reconstructed backtest cannot load another market')
    base=inputs/'reconstructed-primary-inputs'/'TW'
    by_day={}
    for day in days:
        name=str(day.date())
        file=base/f'{name}.json'
        if not file.is_file():
            raise primary_live.ResearchGap(f'Missing Taiwan reconstructed daily input {name}')
        payload=json.loads(file.read_text())
        if (payload['market']!='TW' or payload['cutoff']!=name or
            payload['status']!='RETROSPECTIVE_RECONSTRUCTION_NOT_TRADE_VERIFIED' or
            payload['financialNumericalVintageVerified'] is not False or
            payload['tradeReplayVerified'] is not False or
            payload['reconstructedCount']!=80 or payload['issues'] or len(payload['stocks'])!=80):
            raise ValueError(f'Invalid Taiwan reconstruction source {name}')
        by_day[day]=payload
    return by_day

def rebuild_account_scenarios(research, destination):
    research=Path(research)
    release=json.loads((research/'release.json').read_text())
    marker=json.loads((research/'filing-evidence/TW/reconstructed-daily/status.json').read_text())
    if(marker['status']!='COMPLETE_RESEARCH_RECONSTRUCTION' or
       marker['strategyTradingReplayVerified'] is not False or
       marker['through']!='2026-11-16'):
        raise ValueError('Retrospective 80-company report and quote inputs incomplete')
    target=Path(destination)
    original_loader=primary_live.load_archive
    try:
        primary_live.load_archive=load_reconstructed
        with TemporaryDirectory() as work:
            temp=Path(work)
            # The official account checkpoint is loaded from the versioned
            # original seed, never copied from conditional fixed-price marks.
            result=primary_live.evaluate_market(research,release,temp,'TW')
            if result['status']!='VERIFIED':
                raise primary_live.ResearchGap(
                    'Original Taiwan A/B engine could not reconstruct complete daily trades: '+json.dumps(result))
            source=temp/'primary'/'TW'
            candidate=json.loads((source/'release.json').read_text())
            keys={(row['year'],row['variant']) for row in candidate['summary']}
            if (candidate['through']!='2026-11-16' or len(candidate['summary'])!=22 or
                keys!={(year,variant) for year in range(2016,2027) for variant in 'AB'}):
                raise ValueError('Reconstructed strategy output is incomplete')
            for curve in candidate['curves'].values():
                if not curve or curve[0][0]<'2026-11-05' or curve[-1][0]!='2026-11-16':
                    raise ValueError('Reconstructed NAV curve dates invalid')
            ledgers=json.loads((source/'ledgers'/'2026-11-16.json').read_text())
            for account,log in ledgers['accounts'].items():
                for order in log['orders']:
                    if (order.get('signal_date') is not None and order.get('date') is not None and
                        str(order['signal_date'])>=str(order['date'])):
                        raise ValueError('Lookahead reconstructed trade found in '+account)
            document={
                'schema':1,'market':'TW','through':'2026-11-16','anchor':'2026-11-04',
                'status':RECON_STATUS,'verifiedOfficialTradingPerformance':False,
                'officialReportUploadTimesVerified':True,
                'allHistoricalPricesAvailable':True,
                'historicalVendorValuesAtOriginalCloseVerified':False,
                'tradingRules':'Original sealed Taiwan A/B continuation kernel, unmodified',
                'explanation':'Actual PDF upload dates select the fiscal quarter. Vendor income and cashflow values were downloaded on 2026-11-17; prior-day numerical revisions and cashflows have not been independently attested to contemporaneous filings. Results are illustrative and must not replace the 11/4 official verified ledger.',
                'summary':candidate['summary'],'curves':candidate['curves'],'latest':candidate['latest'],
                'sourceEvidence':['filing-evidence/TW/official-book-upload-review-2026-11-17.json',
                    'filing-evidence/TW/daily-availability/2026-11-05_to_2026-11-16.json',
                    'filing-evidence/TW/replay-source/cashflow-reconstructed-2026-11-17.json',
                    'filing-evidence/TW/reconstructed-daily/status.json']
            }
            target.mkdir(parents=True,exist_ok=True)
            (target/'release.json').write_text(json.dumps(document,ensure_ascii=False,allow_nan=False)+'\n')
            (target/'ledger.json').write_text(json.dumps(ledgers,ensure_ascii=False,allow_nan=False)+'\n')
            return {'status':RECON_STATUS,'through':'2026-11-16',
                'cohorts':len(candidate['summary']),
                '2026':[{k:row[k] for k in ('year','variant','cumulative','max_drawdown','orders','positions')}
                 for row in candidate['summary'] if row['year']==2026],
                'verifiedOfficialTradingPerformance':False,
                '2026NewOrders':{key:len(log['orders']) for key,log in ledgers['accounts'].items()
                                 if key in ('TW_2026_A','TW_2026_B')},
                '2026NewDaily':{key:len(log['daily']) for key,log in ledgers['accounts'].items()
                                 if key in ('TW_2026_A','TW_2026_B')}}
    finally:
        primary_live.load_archive=original_loader

if __name__=='__main__':
    import sys
    if len(sys.argv)!=3:
        raise SystemExit('Usage: python performance/tw_provisional_replay.py RESEARCH_DIR OUTPUT_DIR')
    print(json.dumps(rebuild_account_scenarios(sys.argv[1],sys.argv[2]),ensure_ascii=False,allow_nan=False))
