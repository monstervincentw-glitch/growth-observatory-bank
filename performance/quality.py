"""Separate calculation success, input evidence and workflow health (offline only)."""
import argparse
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path

HISTORICAL_TW = ('2026-11-04','2026-11-05','2026-11-06','2026-11-09','2026-11-10',
                 '2026-11-11','2026-11-12','2026-11-13','2026-11-16')
HEALTHY = {'VERIFIED','NO_NEW_DATA','CALCULATED_EVIDENCE_QUALIFIED'}


def audit_tw(inputs, output):
    release = json.loads((output/'primary/TW/release.json').read_text())
    proof_path=inputs/'financial-numerical-evidence/TW.json'
    numeric=json.loads(proof_path.read_text()) if proof_path.exists() else {}
    calculated = {d for points in release['curves'].values() for d,_ in points}
    rows=[]
    for day in HISTORICAL_TW:
        path=inputs/'reconstructed-primary-inputs/TW'/f'{day}.json'
        if not path.exists():
            rows.append(dict(day=day,evidence='MISSING',pointInTimeFullyVerified=False))
            continue
        raw=path.read_bytes(); archive=json.loads(raw)
        stocks=archive['stocks']
        close=datetime.fromisoformat(day+'T13:30:00+08:00')
        pdf=sum(bool((d:=s.get('officialPDF',{})).get('uploadedAt')) and
                datetime.fromisoformat(d['uploadedAt'])<close for s in stocks.values())
        numerical=sum(s.get('originalVendorNumericalVintageVerified') is True for s in stocks.values())
        supplied=[numeric.get('stocks',{}).get(s,{}).get((v.get('financialVersion') or {}).get('period'),{}) for s,v in stocks.items()]
        rows.append(dict(day=day,evidence='RETROSPECTIVE_RECONSTRUCTION',stocks=len(stocks),
            officialReportTimeAvailable=pdf,historicalNumericalVersionVerified=numerical,
            officialNumericValuesVerified=sum(v.get('strategyFieldsVerified') is True for v in supplied),
            officialYtdIncomeComponentsMatched=sum(len(v.get('officialYtdIncome',{}))==2 for v in supplied),
            pointInTimeFullyVerified=(len(stocks)==80 and pdf==80 and numerical==80),
            observedAt=archive['observedAt'],sourceIncomeObservedAt=archive.get('sourceIncomeObservedAt'),
            sourceCashflowObservedAt=archive.get('sourceCashflowObservedAt'),
            sourcePath=str(path.relative_to(inputs)),sourceSha256=sha256(raw).hexdigest(),
            calculationInPublishedCurve=(day in calculated or day=='2026-11-04'),
            calculationRole='SEALED_BASELINE' if day=='2026-11-04' else 'PUBLISHED_CONTINUATION'))
    # The historical baseline itself explicitly uses estimated statutory release
    # dates. Advancing it does not retroactively certify its PIT evidence.
    return dict(schema=1,market='TW',through=release['through'],
        status='QUALIFIED_RECONSTRUCTED_HISTORY',calculation='PUBLISHED_ENGINE_CHECKS_PASSED',
        pointInTimeFullyVerified=False,historyRewritten=False,
        baseline=dict(through='2026-11-04',financialDates='ESTIMATED_STATUTORY_DEADLINE_PLUS_10_DAYS',
                      historicalPointInTimeVerified=False,researchOnly=True),
        sessions=rows,numericEvidenceProgress=numeric.get('progress',{}),
        note='計算通過不代表財報歷史數值版本及當時可得性完整驗證；既有報酬保留。')


def qualify_tw(result, evidence):
    result['evidenceStatus']=evidence['status']
    result['pointInTimeFullyVerified']=False
    result['evidencePath']='quality/TW-evidence.json'
    result['numericEvidenceProgress']=evidence.get('numericEvidenceProgress',{})
    result['calculationStatus']=result['status']
    if result['status'] in {'VERIFIED','NO_NEW_DATA'}:
        result['status']='CALCULATED_EVIDENCE_QUALIFIED'
    return result


def check_scope(report, scope):
    row=report['results'][scope]
    ok=row.get('status') in HEALTHY and not row.get('valuationError')
    if ok and row.get('requested') and row.get('through')<row['requested']:
        ok=False
    message=f"{scope}: {row.get('status')} · through={row.get('through')} · requested={row.get('requested')}"
    if not ok:
        print('::error::'+message+' · '+str(row.get('detail') or row.get('valuationError') or ''))
    elif row.get('evidenceStatus')=='QUALIFIED_RECONSTRUCTED_HISTORY':
        print('::warning::'+message+' · 歷史財報數值版本仍待驗證')
    else:print(message)
    return ok


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('status',type=Path);p.add_argument('scope',choices=['ETF','US','TW'])
    a=p.parse_args();raise SystemExit(0 if check_scope(json.loads(a.status.read_text()),a.scope) else 1)
