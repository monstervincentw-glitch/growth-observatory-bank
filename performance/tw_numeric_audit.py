"""Offline, deduplicated numeric evidence audit. Never replay or alter accounts."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from hashlib import sha256
import csv
import json
from pathlib import Path

FIELDS=('revenue_ttm','revenue_growth','quarterly_growth','operating_margin','cfo_margin',
        'prior_release_growth','prior_release_margin')
WINDOW=('2026-11-04','2026-11-05','2026-11-06','2026-11-09','2026-11-10',
        '2026-11-11','2026-11-12','2026-11-13','2026-11-16')
INCOME='financial-source-rows/TW/2026-11-16--44012645235_1_TW.json'
CASH='filing-evidence/TW/replay-source/cashflow-reconstructed-2026-11-17.json'


def load(path):return json.loads(path.read_text())
def digest(path):return sha256(path.read_bytes()).hexdigest()
def number(x):return Decimal(str(x))
def quarter_end(year,q):return f'{year}-{q*3:02d}-{31 if q in (1,4) else 30}'


def official_facts(transcript,documents):
    """Official facts are transcribed independently, including exact page/columns."""
    if transcript['unit']!='NTD_THOUSANDS':raise ValueError('Unsupported official unit')
    metadata={x['pdf']:x for x in documents}
    quarter,ytd={},{}
    for row in transcript['records']:
        doc=metadata[row['document']]
        if not doc['url'].startswith('https://investor.tsmc.com/') or doc['symbol']!=transcript['symbol']:
            raise ValueError('Issuer PDF provenance mismatch')
        for basis,target in [('quarters',quarter),('ytd',ytd)]:
            for period,values in row[basis].items():
                for metric,value in values.items():
                    fact=dict(value=str(number(value)*1000),unit='NTD',sourceValueThousands=value,
                        document=row['document'],pdfSha256=doc['sha256'],url=doc['url'],
                        page=row['cashflowPage'] if metric=='operatingCashflow' else row['incomePage'],
                        period=period,basis='QUARTER' if basis=='quarters' else 'YTD',
                        calculation='official statement amount × 1000',retrievedAt=doc['retrievedAt'])
                    key=(period,metric)
                    if key in target and target[key]['value']!=fact['value']:
                        raise ValueError('Conflicting official PDFs: '+str(key))
                    target.setdefault(key,fact)
    facts={}
    for (period,metric),row in quarter.items():facts[period,metric]=row
    for (period,metric),row in ytd.items():
        if metric=='operatingCashflow':facts[period,metric]=row
        elif period.endswith('03-31'):
            facts.setdefault((period,metric),row|{'basis':'QUARTER'})
        elif period.endswith('12-31'):
            previous=ytd.get((period[:4]+'-09-30',metric))
            if previous:
                facts[period,metric]=row|dict(value=str(number(row['value'])-number(previous['value'])),
                    basis='QUARTER',calculation='official FY YTD minus official Q3 YTD',
                    operands=[row,previous])
    return {(transcript['symbol'],p,m):v for (p,m),v in facts.items()}


def formulas(rows,cash,period):
    dates=[r['date'] for r in rows];end=dates.index(period)
    if end<8:raise ValueError('Prior-release comparison requires nine revenue quarters')
    raw={}
    for r in rows:
        for source,key in [('revenue','revenue'),('operatingIncome','operatingIncome')]:
            if r.get(source) is not None:raw[r['date'],key]=number(r[source])
    for r in cash:
        if r['type'] not in ('CashFlowsFromOperatingActivities','NetCashInflowFromOperatingActivities'):continue
        key=r['date'],'operatingCashflow';value=number(r['value'])
        if key in raw and raw[key]!=value:raise ValueError('Conflicting cashflow aliases')
        raw[key]=value
    def keys(n,metric):return [(d,metric) for d in dates[n-3:n+1]]
    def total(ks):return sum(raw[k] for k in ks)
    current=keys(end,'revenue');prior=keys(end-4,'revenue')
    previous=keys(end-1,'revenue');previous_prior=keys(end-5,'revenue')
    operating=keys(end,'operatingIncome');previous_op=keys(end-1,'operatingIncome')
    cash_keys=[];cash_total=Decimal(0)
    for d in dates[end-3:end+1]:
        key=d,'operatingCashflow';cash_keys.append(key);cash_total+=raw[key]
        q=int(d[5:7])//3
        if q>1:
            key=quarter_end(int(d[:4]),q-1),'operatingCashflow'
            cash_keys.append(key);cash_total-=raw[key]
    with localcontext() as ctx:
        ctx.prec=40
        values=dict(revenue_ttm=total(current),revenue_growth=total(current)/total(prior)-1,
            quarterly_growth=raw[dates[end],'revenue']/raw[dates[end-4],'revenue']-1,
            operating_margin=total(operating)/total(current),cfo_margin=cash_total/total(current),
            prior_release_growth=total(previous)/total(previous_prior)-1,
            prior_release_margin=total(previous_op)/total(previous))
    deps=dict(revenue_ttm=current,revenue_growth=current+prior,
        quarterly_growth=[(dates[end],'revenue'),(dates[end-4],'revenue')],
        operating_margin=current+operating,cfo_margin=current+cash_keys,
        prior_release_growth=previous+previous_prior,prior_release_margin=previous+previous_op)
    return values,{f:sorted(set(k)) for f,k in deps.items()},raw


def audit(root,transcript_path,documents_path,output):
    output.mkdir(parents=True,exist_ok=True)
    income=load(root/INCOME);cash=load(root/CASH)
    documents=load(documents_path)
    official=official_facts(load(transcript_path),documents)
    versions={};sources={INCOME:digest(root/INCOME),CASH:digest(root/CASH)}
    for day in WINDOW:
        path=root/'reconstructed-primary-inputs/TW'/f'{day}.json';data=load(path)
        if data['cutoff']!=day or len(data['stocks'])!=80:raise ValueError('Incomplete historical scope')
        sources[str(path.relative_to(root))]=digest(path)
        for symbol,stock in data['stocks'].items():
            key=symbol,stock['financialVersion']['period']
            row=versions.setdefault(key,dict(symbol=symbol,period=key[1],name=stock['name'],days=[],
                financial={f:stock['financial'][f] for f in FIELDS},officialPDF=stock['officialPDF']))
            if row['financial']!={f:stock['financial'][f] for f in FIELDS}:
                raise ValueError('Conflicting numbers in same historical version: '+str(key))
            row['days'].append(day)
    prior_path=root/'filing-evidence/TW/reconciliation/2026-11-17.json';prior=load(prior_path)
    sources[str(prior_path.relative_to(root))]=digest(prior_path)
    upload_path=root/'filing-evidence/TW/official-book-upload-review-2026-11-17.json'
    uploads=load(upload_path)
    sources[str(upload_path.relative_to(root))]=digest(upload_path)
    registered={(d['symbol'],d['period'],d['document']):d for d in uploads['selected']}
    for version in versions.values():
        original=version['officialPDF']
        proof=registered[version['symbol'],version['period'],original['file']]
        if proof['uploadedAt']!=original['uploadedAt']:raise ValueError('Existing timestamp evidence differs')
    # Read only the already-published ledger to prioritize the actual signal
    # inputs; no account or performance calculation is executed.
    roles={}
    ledger_path=Path(__file__).resolve().parents[1]/'docs/financial-numeric-audit/strategy-use-priority.json'
    if ledger_path.exists():roles=load(ledger_path)['symbols']
    old_records=[]
    for r in prior['records']:
        for metric,legacy in [('revenue','revenue'),('operatingIncome','operatingIncome')]:
            field=r[legacy];vendor=sum(number(q[metric]) for q in income['stocks'][r['symbol']]['quarters']
                if q['date'] in r['comparedQuarters'])/1000
            official_value=number(field['officialYtdThousands'])
            if vendor!=official_value or not field['matched']:raise ValueError('Existing comparison does not cover frozen input')
            old_records.append(dict(symbol=r['symbol'],period=r['period'],field=metric,
                officialYtdThousands=str(official_value),frozenFinmindYtdThousands=str(vendor),
                reused=True,sourcePath=str(prior_path.relative_to(root)),
                evidence='OFFICIAL_LATEST_EXCHANGE_SNAPSHOT_NOT_ORIGINAL_PDF',
                originalHistoricalVersionVerified=False))
    metrics=[];facts={}
    for (symbol,period),version in sorted(versions.items()):
        version['strategyUse']=roles.get(symbol,{'priority':3,'role':'EVALUATED_IN_80_STOCK_POOL'})
        calculated,deps,raw=formulas(income['stocks'][symbol]['quarters'],cash['stocks'][symbol]['rows'],period)
        all_deps=sorted(set(k for ks in deps.values() for k in ks))
        for d,field in all_deps:
            key=symbol,d,field;fact=facts.setdefault(key,dict(symbol=symbol,period=d,field=field,
                finmindValueNTD=str(raw[d,field]),versions=[],official=None,status='PENDING_ORIGINAL_OFFICIAL_NUMBERS'))
            if period not in fact['versions']:fact['versions'].append(period)
            proof=official.get(key)
            if proof:
                fact.update(official=proof,officialValueNTD=proof['value'],deltaNTD=str(raw[d,field]-number(proof['value'])),
                    status='OFFICIAL_ISSUER_PDF_MATCH' if raw[d,field]==number(proof['value']) else 'VALUE_MISMATCH')
        version_rows=[]
        for field in FIELDS:
            stored=number(version['financial'][field]);value=calculated[field]
            # Exact raw monetary comparison. Derived ratios use 1e-12 only to
            # compare the existing IEEE-754 output with independent Decimal math.
            arithmetic_ok=abs(stored-value)<= (Decimal(0) if field=='revenue_ttm' else Decimal('1e-12'))
            if not arithmetic_ok:raise ValueError(f'{symbol} {period} {field}: archived formula differs')
            evidence=[facts[symbol,d,k] for d,k in deps[field]]
            matched=all(e['status']=='OFFICIAL_ISSUER_PDF_MATCH' for e in evidence)
            mismatch=any(e['status']=='VALUE_MISMATCH' for e in evidence)
            row=dict(symbol=symbol,name=version['name'],period=period,field=field,days=version['days'],
                archivedValue=version['financial'][field],independentCalculation=str(value),arithmeticMatched=True,
                rawDependencies=[dict(period=d,field=k) for d,k in deps[field]],
                officialStatus='VALUE_MISMATCH' if mismatch else 'OFFICIAL_ISSUER_PDF_MATCH' if matched else 'PENDING_DEPENDENCIES',
                originalPdf=version['officialPDF'],originalMopsPdfByteIdentityVerified=False,
                historicalPointInTimeFullyVerified=False,
                impact='UNCHANGED_INPUT_VALUE' if matched else 'NOT_DETERMINABLE_UNTIL_REQUIRED_OFFICIAL_VALUES_AVAILABLE')
            metrics.append(row);version_rows.append(row)
        version['numericStatus']=('OFFICIAL_ISSUER_PDF_VALUES_MATCHED_ORIGINAL_VERSION_LINK_PENDING'
            if all(r['officialStatus']=='OFFICIAL_ISSUER_PDF_MATCH' for r in version_rows) else 'PENDING_NUMERIC_DEPENDENCIES')
        version['historicalPointInTimeFullyVerified']=False
    facts=list(facts.values());matched=sum(r['officialStatus']=='OFFICIAL_ISSUER_PDF_MATCH' for r in metrics)
    report=dict(schema=1,generatedAt=datetime.now(timezone.utc).isoformat(),window=list(WINDOW),
        status='PARTIAL_NUMERIC_RECONCILIATION',previous=dict(officialUploadCompanies=uploads['succeeded'],
            officialUploadSelected2026Reports=len(uploads['selected']),latestYtdIncomeComparisonsReused=len(old_records),
            originalPdfDependencyCompleteVersions=0),
        scope=dict(companies=80,companyDays=720,uniqueCompanyPeriods=len(versions),
            distinctRequiredRawFacts=len(facts),distinctDerivedFields=len(metrics)),
        added=dict(rawFactsMatched=sum(r['status']=='OFFICIAL_ISSUER_PDF_MATCH' for r in facts),
            rawFactMismatches=sum(r['status']=='VALUE_MISMATCH' for r in facts),
            derivedFieldsMatched=matched,derivedFieldMismatches=sum(r['officialStatus']=='VALUE_MISMATCH' for r in metrics),
            issuerPdfMatchedVersions=sum(v['numericStatus'].startswith('OFFICIAL_ISSUER') for v in versions.values()),
            originalPdfVersionIdentityFullyVerified=0,arithmeticChecksPassed=len(metrics)),
        pending=dict(rawFacts=sum(r['status']=='PENDING_ORIGINAL_OFFICIAL_NUMBERS' for r in facts),
            derivedFields=sum(r['officialStatus']=='PENDING_DEPENDENCIES' for r in metrics),
            numericVersions=sum(v['numericStatus']=='PENDING_NUMERIC_DEPENDENCIES' for v in versions.values()),
            originalPdfVersionIdentity=len(versions)),
        network=dict(originalAnnouncementTimestampRequests=0,finmindRequests=0,fredRequests=0,
            mopsPdfRequests=1,mopsBlocked=True,mopsRetries=0,
            mopsBlock='FOR SECURITY REASONS, THIS PAGE CAN NOT BE ACCESSED',issuerPdfsDownloaded=7),
        sources=sources,evidenceFiles=dict(transcriptionSha256=digest(transcript_path),
            documentManifestSha256=digest(documents_path)),formulaSource='engine/providers.ts::twFinancial',
        formulae=dict(revenue_ttm='sum Revenue latest 4 quarters',revenue_growth='TTM Revenue / year-ago TTM Revenue - 1',
            quarterly_growth='latest single-quarter Revenue / year-ago single-quarter Revenue - 1',
            operating_margin='sum OperatingIncome latest 4 quarters / TTM Revenue',
            cfo_margin='sum quarter CFO (YTD differenced within fiscal year; Q1 unchanged) / TTM Revenue',
            prior_release_growth='same TTM revenue YoY formula at previous quarter',
            prior_release_margin='same operating margin formula at previous quarter'),
        performanceReplayed=False,performanceModified=False,originalTimestampsModified=False,
        tradingRulesModified=False,schedulesModified=False,globalHistoricalVerificationUpgraded=False)
    report['perDay']=[dict(day=day,companyInputs=80,
        issuerPdfMatched=sum(day in v['days'] and v['numericStatus'].startswith('OFFICIAL_ISSUER') for v in versions.values()),
        pending=sum(day in v['days'] and v['numericStatus']=='PENDING_NUMERIC_DEPENDENCIES' for v in versions.values()),
        originalMopsPdfVersionLinked=0) for day in WINDOW]
    payloads={'latest.json':report,'versions.json':list(versions.values()),'raw-facts.json':facts,
              'derived-fields.json':metrics,'reused-comparisons.json':old_records}
    for name,data in payloads.items():
        (output/name).write_text(json.dumps(data,ensure_ascii=False,separators=(',',':'),allow_nan=False)+'\n')
    for name,rows,fields in [('raw-facts.csv',facts,['symbol','period','field','finmindValueNTD','officialValueNTD','deltaNTD','status']),
        ('derived-fields.csv',metrics,['symbol','name','period','field','archivedValue','independentCalculation','officialStatus','impact'])]:
        with (output/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(rows)
    queue=sorted(versions.values(),key=lambda v:(v['strategyUse']['priority'],v['symbol'],v['period']))
    (output/'pending-versions.json').write_text(json.dumps([v for v in queue if v['numericStatus']=='PENDING_NUMERIC_DEPENDENCIES'],
        ensure_ascii=False,separators=(',',':'))+'\n')
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('research',type=Path);p.add_argument('transcription',type=Path)
    p.add_argument('documents',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    print(json.dumps(audit(a.research,a.transcription,a.documents,a.output),ensure_ascii=False,indent=2))
