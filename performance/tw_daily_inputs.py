"""Recover Taiwan continuation inputs only from versioned, close-eligible archives.

No network access, new fiscal estimates, CPI overlays, or trading-rule changes.
The original A/B engine uses the original weekly ALFRED model, independently of
live daily macro diagnostics. Preserve both sources and the actual recovery time.
"""
from datetime import datetime, timezone
from hashlib import sha256
import gzip
import json
import math
from pathlib import Path

import pandas as pd

FIELDS = ('financial_status','latest_period','revenue_growth','quarterly_growth',
          'operating_margin','cfo_margin','revenue_ttm','fundamental_release_key',
          'prior_release_growth','prior_release_margin')


def digest(raw):
    return sha256(raw).hexdigest()


def read(path):
    raw = path.read_bytes()
    return json.loads(raw), digest(raw)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def timestamp(value):
    result = pd.Timestamp(value)
    require(result.tzinfo is not None, 'Availability timestamp has no timezone')
    return result


def build_input(observation, *, universe, funding, weekly, source_sha, weekly_sha,
                source_path, weekly_path, recovered_at):
    from primary_live import point_in_time
    day = observation['day']
    close = timestamp(day+'T13:30:00+08:00')
    require(observation.get('schema') == 1 and observation.get('market') == 'TW'
            and observation.get('status') == 'PRICES_VERIFIED'
            and timestamp(observation['decisionAt']) == close, 'Unverified Taiwan observation')
    require(set(observation['quotes']) == set(universe) == set(observation['observations'])
            and len(universe) == 80 and observation.get('expected') == 80
            and not observation.get('inactive'), 'Taiwan universe is incomplete')
    require(str(funding['date'].date()) == weekly['day'] and weekly['source'] == 'ALFRED'
            and weekly['day'] < day and timestamp(weekly['observedAt']) <= close,
            'Original weekly macro was not available by Taiwan close')
    require(set(weekly['series']) == {'CPIAUCSL','DFII10','FEDFUNDS','NFCI'}
            and all(f'{s}_{weekly["day"].replace("-", "")}' in raw.splitlines()[0]
                    for s, raw in weekly['series'].items()), 'Original ALFRED vintage identity mismatch')
    stocks = {}
    for symbol in sorted(universe):
        quote, observed = observation['quotes'][symbol], observation['observations'][symbol]
        bars = quote.get('bars') or []
        require(quote.get('priceDate') == day and quote.get('priceValidation', {}).get('cutoff') == day
                and not quote.get('financeError') and len(bars) >= 2,
                symbol+': quote verification missing')
        dates = [row['date'] for row in bars]
        require(dates == sorted(set(dates)) and dates[-1] == day,
                symbol+': quote dates incomplete or forward-dated')
        for bar in bars:
            require(all(number(bar.get(k)) and bar[k] > 0 for k in ('open','close','adjOpen','adjClose','volume')),
                    symbol+': invalid raw or adjusted price')
            require(abs(bar['adjOpen']/bar['adjClose']-bar['open']/bar['close']) <= 1e-8,
                    symbol+': adjustment basis inconsistent')
        require(abs(quote['close']-bars[-1]['close']) <= 1e-6*max(1,quote['close']),
                symbol+': quote endpoint mismatch')
        version = observed.get('financialVersion') or {}
        financial = observed.get('financial') or {}
        require(version.get('id') == quote.get('financialVersion') and version.get('source') == 'FinMind'
                and financial.get('financial_status') == 'ok'
                and financial.get('latest_period') == version.get('period')
                and all(financial.get(k) == version.get('financial', {}).get(k) for k in FIELDS),
                symbol+': fiscal numerical version does not match archived source')
        require(timestamp(version['firstVerifiedAt']) <= timestamp(observation['observedAt'])
                and timestamp(version['eligibleFrom']) <= close, symbol+': fiscal version unavailable at close')
        features, flags = observed['features'], observed['evaluation']
        require(all(number(features.get(k)) for k in
                    ('history_sessions','dollar_volume','drawdown_126','relative_63')),
                symbol+': price indicator missing')
        tradable = features['history_sessions'] >= 252 and features['dollar_volume'] >= 3e8
        divergence = -.5 <= features['drawdown_126'] <= -.1 and features['relative_63'] < 0
        require(flags['tradable'] is tradable and flags['divergence'] is divergence,
                symbol+': original technical eligibility mismatch')
        stocks[symbol] = dict(financial=financial,financialVersion=version,bars=bars,features=features,
                             evaluation=dict(tradable=tradable,divergence=divergence),
                             source=quote.get('source'),quoteStatus=quote['priceValidation'])
    # Metadata such as industry/name is supplied separately from the same archived
    # full research input, never from a newly downloaded universe or fiscal report.
    result = dict(schema=1,market='TW',cutoff=day,batch=observation['batch'],
                  status='POINT_IN_TIME_INPUT_VERIFIED',decisionAt=observation['decisionAt'],
                  observedAt=observation['observedAt'],recoveredAt=recovered_at,
                  verificationBasis='ARCHIVED_PIT_FINANCIALS_QUOTES_AND_ORIGINAL_WEEKLY_MACRO',
                  sourceEvidence=dict(observationPath=source_path,observationSHA256=source_sha,
                                      weeklyPath=weekly_path,weeklySHA256=weekly_sha),
                  strategyMacro=dict(vintage=weekly['day'],source='ORIGINAL_WEEKLY_ALFRED',
                                     scoreRange=list(funding['score_range'] or (funding['score'],funding['score'])),
                                     decisionProxyScore=funding['score'],
                                     note='Existing replay_funding threshold decision; proxy is not a reported CPI or exact observed score'),
                  stocks=stocks)
    require(not point_in_time(result,'TW'), 'Financial availability or quote gate failed')
    return result


def recover_sessions(inputs, output, release, sessions, anchor, *, cfg):
    """Append verified inputs and retain a full compressed input in performance-data."""
    from primary_live import replay_funding, point_in_time
    from live_etf import weekly_history
    from equity_ab import grouped_macro
    from etf import PartialData
    inputs, output = Path(inputs), Path(output)
    cache = output/'primary/TW/input-archive'
    cache.mkdir(parents=True,exist_ok=True)
    recovered_dir = inputs/'recovered-primary-inputs/TW'
    recovered_dir.mkdir(parents=True,exist_ok=True)
    report = dict(schema=1,market='TW',at=datetime.now(timezone.utc).isoformat(),
                  anchor=str(anchor.date()),requested=str(sessions[-1].date()),recovered=[],existing=[],gaps={})
    universe = {s['symbol']:s for s in release['snapshot']['markets']['TW']['stocks']}
    try:
        weekly_rows = weekly_history(inputs,sessions[-1])[0]
        grouped = grouped_macro(weekly_rows,'base').set_index('macro_vintage_date')
    except (PartialData,ValueError,KeyError,OSError) as error:
        report['gaps']['macro'] = str(error)
        grouped = None
    for date in sessions.insert(0,anchor):
        day = str(date.date())
        if any(any((inputs/folder/'TW'/f'{day}{suffix}').exists() for suffix in ('.json','.json.gz')) for folder in
               ('observed-primary-inputs','primary-inputs')):
            report['existing'].append(day)
            continue
        try:
            source = inputs/'market-observations/TW'/f'{day}.json'
            observation, source_sha = read(source)
            require(grouped is not None,'Original weekly macro chain is unavailable')
            available = grouped.loc[:date-pd.Timedelta(days=1)]
            require(len(available)>0,'Original weekly macro date is unavailable')
            funding = replay_funding(available.iloc[-1],cfg)
            require(funding is not None,'Original weekly macro cannot determine both A/B gates')
            vintage = str(funding['date'].date())
            weekly_path = inputs/'weekly-vintages'/f'{vintage}.json'
            if not weekly_path.exists() and vintage == '2026-11-06':
                weekly_path = Path(__file__).parent/'seed-weekly'/f'{vintage}.json'
            weekly, weekly_sha = read(weekly_path)
            current, current_sha = read(inputs/'unverified-primary-inputs/TW'/f'{day}.json')
            require(current['market']=='TW' and current['cutoff']==day and set(current['stocks'])==set(universe),
                    'Archived instrument metadata is incomplete')
            dest = cache/f'{day}.json.gz'
            if dest.exists():
                payload = json.loads(gzip.decompress(dest.read_bytes()))
                require(payload['sourceEvidence']['observationSHA256']==source_sha
                        and payload['sourceEvidence']['weeklySHA256']==weekly_sha
                        and payload['sourceEvidence']['metadataSHA256']==current_sha,
                        'An immutable input source changed; preserve existing archive for review')
                require(not point_in_time(payload,'TW'),'Cached input no longer passes PIT checks')
            else:
                payload = build_input(observation,universe=universe,funding=funding,weekly=weekly,
                                      source_sha=source_sha,weekly_sha=weekly_sha,
                                      source_path=str(source.relative_to(inputs)),
                                      weekly_path=str(weekly_path.relative_to(inputs)) if weekly_path.is_relative_to(inputs) else 'performance/seed-weekly/'+weekly_path.name,
                                      recovered_at=report['at'])
                for symbol, stock in payload['stocks'].items():
                    stock.update(industry=current['stocks'][symbol]['industry'],name=current['stocks'][symbol]['name'])
                payload['sourceEvidence'].update(metadataPath=f'unverified-primary-inputs/TW/{day}.json',metadataSHA256=current_sha)
                # Only the original weekly model is used for the continuation.
                # Live CPI research overlays remain in the untouched source file.
                payload['macro']=dict(status='ORIGINAL_WEEKLY_THRESHOLD_DECISION_VERIFIED',**payload['strategyMacro'])
                raw=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
                with dest.open('xb') as file:
                    file.write(gzip.compress(raw,mtime=0))
            target=recovered_dir/f'{day}.json'
            raw=json.dumps(payload,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode()
            if target.exists():
                require(json.loads(target.read_bytes())==payload,'Conflicting recovered input already exists')
            else:
                with target.open('xb') as file:file.write(raw+b'\n')
            report['recovered'].append(dict(date=day,instruments=len(payload['stocks']),
                                          sourceObservationSHA256=source_sha,inputSHA256=digest(raw),
                                          macro=payload['strategyMacro']))
        except (ValueError,KeyError,TypeError,OSError,IndexError) as error:
            report['gaps'][day]=type(error).__name__+': '+str(error)
    report['status']='COMPLETE' if not report['gaps'] else 'WAITING_FOR_VERIFIABLE_INPUTS'
    (output/'primary/TW/input-recovery.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    return report
