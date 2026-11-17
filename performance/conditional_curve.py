"""Publish a transparent fixed-holdings NAV continuation, never a strategy replay.

The sealed A/B trading ledger remains stopped at its verified date. A conditional
curve marks those exact sealed positions and cash against post-seal verified
closing prices WITHOUT ANY simulated transactions or exits.
"""
from hashlib import sha256
import json
import math
from pathlib import Path


def _positive(value, context):
    if not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
        raise ValueError('Invalid conditional NAV value: '+context)
    return float(value)


def build_conditional_curve(directory, baseline, market, expected_dates=None):
    """Require identical year/variant, all expected sessions, and reconciled NAV."""
    directory=Path(directory)
    anchor=baseline['date']
    accounts=baseline[market]
    by_account={(str(a['year']),a['variant']):a for a in accounts}
    if len(accounts)!=len(by_account) or len(accounts)!=(22 if market=='TW' else 42):
        raise ValueError('Unexpected sealed account universe')
    latest=directory/'latest.json'
    if not latest.exists():
        return None
    saved=json.loads(latest.read_text())
    if saved.get('schema')!=1 or saved.get('market')!=market or saved.get('anchor')!=anchor or saved.get('status')!='CONDITIONAL_FIXED_HOLDINGS':
        raise ValueError('Conditional last mark differs from sealed account basis')
    last=saved['through']
    files=sorted((directory/'sessions').glob('????-??-??.json'))
    dates=[file.stem for file in files if anchor<file.stem<=last]
    if not dates or dates[-1]!=last or len(set(dates))!=len(dates):
        raise ValueError('Incomplete conditional daily NAV sessions')
    if expected_dates is not None and dates!=sorted(expected_dates):
        raise ValueError('Conditional day series missing required verified market sessions')

    curves={f'{market}_{year}_{plan}':[[anchor,
      _positive(account['nav'],'sealed NAV')/_positive(account['capital'],'sealed capital')]]
      for (year,plan),account in by_account.items()}
    price_hashes={}
    latest_values={}
    for day,file in zip(dates,[directory/'sessions'/f'{d}.json' for d in dates]):
        mark=json.loads(file.read_text())
        if (mark.get('schema')!=1 or mark.get('status')!='CONDITIONAL_FIXED_HOLDINGS' or
            mark.get('through')!=day or mark.get('anchor')!=anchor or mark.get('market')!=market or
            not isinstance(mark.get('priceHash'),str) or len(mark['priceHash'])!=64):
            raise ValueError('Conditional daily mark provenance mismatch on '+day)
        price_hashes[day]=mark['priceHash']
        seen={}
        for year,rows in mark['years'].items():
            for row in rows:
                key=(str(year),row['variant'])
                if key in seen or key not in by_account:
                    raise ValueError('Duplicate or unknown conditional account on '+day)
                seen[key]=row
        if set(seen)!=set(by_account):
            raise ValueError('Incomplete conditional cohorts on '+day)
        for (year,plan),account in by_account.items():
            row=seen[year,plan]
            cash=account['cash']
            if not math.isclose(row['cash'],cash,rel_tol=0,abs_tol=1e-6):
                raise ValueError('Conditional cash changed after seal')
            holdings={p['symbol']:p for p in account['positions']}
            marks={p['symbol']:p for p in row['positions']}
            if len(holdings)!=len(row['positions']) or set(holdings)!=set(marks):
                raise ValueError('Conditional positions changed after seal')
            for symbol,p in holdings.items():
                if not math.isclose(p['value'],marks[symbol]['value'],rel_tol=1e-9,abs_tol=1e-5):
                    raise ValueError('Conditional original position basis changed')
            actual=cash+sum(_positive(p['markedValue'],symbol+' mark') for symbol,p in marks.items())
            if not math.isclose(actual,row['value'],rel_tol=1e-10,abs_tol=1e-4):
                raise ValueError('Conditional account NAV fails cash/position reconciliation')
            name=f'{market}_{year}_{plan}'
            capital=_positive(account['capital'],name+' capital')
            curves[name].append([day,row['value']/capital])
            latest_values[name]={
                'date':day,'value':float(row['value']),'capital':capital,
                'navMultiple':row['value']/capital,
                'cumulative':row['value']/capital-1,
                'changeSinceSeal':row['value']/_positive(account['nav'],name+' original NAV')-1,
            }

    output={
        'schema':1,'market':market,'status':'CONDITIONAL_FIXED_HOLDINGS',
        'anchor':anchor,'through':last,'noPostAnchorTrading':True,
        'tradingReplayVerified':False,'accountCount':len(by_account),
        'sourcePriceHashes':price_hashes,
        'curves':curves,'latest':latest_values,
        'note':'Post-seal account marks only; positions and cash remain unchanged. Neither missing trades nor strategy profits are recreated.',
    }
    # Every daily session is validated before updating the visible latest curve.
    target=directory/'curve.json'
    target.write_text(json.dumps(output,ensure_ascii=False,allow_nan=False,separators=(',',':'))+'\n')
    return output
