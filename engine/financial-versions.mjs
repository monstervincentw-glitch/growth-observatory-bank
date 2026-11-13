import {createHash} from 'node:crypto';

const metricKeys=['financial_status','latest_period','revenue_growth','quarterly_growth','growth_acceleration',
  'operating_margin','cfo_margin','revenue_ttm','shares','shares_date','shares_filed','max_input_filed',
  'prior_release_growth','prior_release_margin','fundamental_release_key'];

function observe(stock, financial, at, close, source) {
  if(financial?.financial_status!=='ok'||!/^\d{4}-\d{2}-\d{2}$/.test(financial.latest_period??'')||
     financial.latest_period>stock.priceDate||
     !['revenue_growth','quarterly_growth','operating_margin','cfo_margin','revenue_ttm'].every(k=>Number.isFinite(financial[k])))
    return null;
  const id=createHash('sha256').update(JSON.stringify(metricKeys.map(key=>financial[key]??null))).digest('hex');
  const evidence=financial.filingEvidence;
  const official=evidence?.period===financial.latest_period&&evidence.releaseKey===financial.fundamental_release_key&&
    /^https:\/\//.test(evidence.source)&&Number.isFinite(Date.parse(evidence.publishedAt))&&
    Date.parse(evidence.publishedAt)<=Date.parse(at)&&Date.parse(evidence.verifiedThrough)>=Date.parse(close);
  return {id,period:financial.latest_period,officialPublishedAt:official?evidence.publishedAt:null,
    officialSource:official?evidence.source:null,officialFiledDate:stock.market==='US'?financial.max_input_filed??null:null,
    firstVerifiedAt:at,eligibleFrom:official?evidence.publishedAt:at,source,
    verification:official?'OFFICIAL_FILING':'VERIFIED_OBSERVATION',financial};
}

export function financialVersions(stock, previous, existing, close, at) {
  const source=stock.market==='TW'?'FinMind':'SEC Company Facts';
  const versions={...existing?.versions};
  const ingest=(financial, observedAt, decisionClose)=>{
    if(!observedAt||!Number.isFinite(Date.parse(observedAt))||Date.parse(observedAt)>Date.parse(at))return null;
    const version=observe(stock,financial,observedAt,decisionClose,source);
    if(!version)return null;
    const prior=versions[version.id];
    if(!prior)versions[version.id]=version;
    else if(version.officialPublishedAt&&!prior.officialPublishedAt)
      versions[version.id]={...version,firstVerifiedAt:prior.firstVerifiedAt};
    return version.id;
  };
  // Old snapshots can establish an observation time, never an earlier release time.
  if(!Object.keys(versions).length&&previous?.financialObservedAt&&previous.financialObservedAt===previous.observedAt)
    ingest(previous.financial,previous.financialObservedAt,close);
  const observed=stock._financialError?null:ingest(stock.financial,stock.financialObservedAt,close);
  const eligible=Object.values(versions).filter(v=>Date.parse(v.eligibleFrom)<=Date.parse(close))
    .sort((a,b)=>b.period.localeCompare(a.period)||Date.parse(b.firstVerifiedAt)-Date.parse(a.firstVerifiedAt));
  const active=eligible[0]??null;
  const previousId=existing?.active??null;
  const switches=[...(existing?.switches??[])];
  if(active?.id&&active.id!==previousId)
    switches.push({date:stock.priceDate,previousId,nextId:active.id,decisionAt:close,recordedAt:at});
  return {versions,active:active?.id??null,observed,switches};
}
