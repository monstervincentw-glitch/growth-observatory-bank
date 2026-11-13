"""Publish complete performance batches to the bank's public data branch."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import shutil

from etf import PartialData
from live_etf import advance, read_state
from research_inputs import research_release
from primary_valuation import mark_valuation


def save(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')


def record(status, through, requested, detail='', **kwargs):
    return dict(status=status, through=through, requested=requested, detail=detail, **kwargs)


def etf(inputs, release, output):
    previous, _, _ = read_state(output/'etf')
    block = release['snapshot']['markets']['US']
    cutoff = block['macro']['marketAsOf']
    latest = release['attempts'].get('US')
    if latest and latest['status'] == 'failed':
        return record('SOURCE_ERROR',previous['through'],cutoff,latest['detail'])
    path = inputs/'etf-inputs'/f'{cutoff}.json'
    if not path.exists():
        status = 'NO_NEW_DATA' if cutoff == previous['through'] else 'PARTIAL_DATA'
        return record(status,previous['through'],cutoff,'ETF market inputs not archived for this close')
    try:
        feed = json.loads(path.read_text())
        if feed['schema'] != 1 or feed['cutoff'] != cutoff:
            raise ValueError('ETF input does not match the US research cutoff')
        if latest and feed['batch'] != latest['batch']:
            # A same-close retry can be recorded in a later combined market batch.
            # Accept the archive only if its batch belongs to an existing verified US session.
            primary = inputs/'primary-inputs'/'US'/f'{cutoff}.json'
            if not primary.exists() or json.loads(primary.read_text())['batch'] != feed['batch']:
                raise ValueError('ETF research batch mismatch')
        if feed['status']!='VERIFIED':
            raise PartialData(str(feed['errors']))
        next_state = output/'next-etf'
        result = advance(inputs,feed,output/'etf',next_state)
        evidence=dict(input_sha256=sha256(path.read_bytes()).hexdigest(), source_batch=feed['batch'],
                      symbols=len(feed['prices']))
        if result is None:
            return record('NO_NEW_DATA',previous['through'],cutoff,**evidence)
        ledger = json.loads((next_state/'ledger.json').read_text())
        save(output/'etf-ledger'/f'{result["through"]}.json',ledger)
        (next_state/'ledger.json').unlink()
        for item in next_state.iterdir():
            item.replace(output/'etf'/item.name)
        next_state.rmdir()
        return record('VERIFIED',result['through'],cutoff,**evidence, cohorts=len(result['summary']),
                      daily=len(ledger['daily']), trades=len(ledger['trades']))
    except PartialData as exc:
        return record('PARTIAL_DATA',previous['through'],cutoff,str(exc))
    except (AssertionError, KeyError, ValueError, TypeError) as exc:
        return record('VALIDATION_FAILED',previous['through'],cutoff,str(exc)[:700])


def primary(inputs, release, output, market):
    from primary import ResearchGap
    from primary_live import evaluate_market
    try:
        return evaluate_market(inputs, release, output, market)
    except ResearchGap as exc:
        cutoff=release['snapshot']['markets'][market]['macro']['marketAsOf']
        path=output/'primary'/market/'release.json'
        through=json.loads(path.read_text())['through'] if path.exists() else '2026-11-04'
        return record('WAITING_FOR_CONTINUITY',through,cutoff,str(exc))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('inputs', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--run', required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    if not (args.output/'etf/manifest.json').exists():
        shutil.copytree(Path(__file__).parent/'seed',args.output/'etf')
    release = research_release(args.inputs/'release.json')
    release['attempts'] = json.loads((args.inputs/'attempts.json').read_text())
    result = {}
    for scope in ('ETF','US','TW'):
        try:
            result[scope] = (etf(args.inputs,release,args.output) if scope=='ETF' else
                             primary(args.inputs,release,args.output,scope))
        except Exception as exc:
            result[scope] = record('SOURCE_ERROR',None,None,type(exc).__name__+': '+str(exc)[:500])
    for market in ('US','TW'):
        try:
            result[market]['valuationThrough']=mark_valuation(args.inputs,release,args.output,market)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result[market]['valuationError']=str(exc)[:400]
    report = dict(schema=1, run=args.run, at=datetime.now(timezone.utc).isoformat(), results=result)
    save(args.output/'status.json',report)
    save(args.output/'runs'/f'{args.run}.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
