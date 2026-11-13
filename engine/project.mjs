import {readFile, writeFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {gzipSync} from 'node:zlib';
import {estimatePortfolios} from './tracking.ts';

const publicFields = {
  stock: ['key','market','symbol','name','industry','ticker','cik','benchmark','currency','priceDate','close','observedAt','sourceQuality','integrity','financialObservedAt','lastAttemptAt','priceSource','sourceWarning','sourceError'],
  finance: ['financial_status','revenue_growth','quarterly_growth','growth_acceleration','operating_margin','cfo_margin','revenue_ttm','shares','shares_date','shares_filed','latest_period','max_input_filed','financial_age_days','fundamental_release_key','financial_event_date','date_basis','prior_release_growth','prior_release_margin'],
  features: ['history_sessions','dollar_volume','drawdown_126','relative_63','market_cap_estimate','price_sales'],
  evaluation: ['fundamental','filingVerified','funding','tradable','divergence','fresh','primary','buy_hold'],
  macro: ['status','ruleVersion','marketAsOf','macro_vintage_date','observedAt','calculatedAt','macro_stress','legacyStress','decision','lastDecisionAt','lastMarketPublishedAt','marketComplete','lastAttemptAt','cpi_yoy','cpi_3m_annualized','real_yield','real_yield_90d_change','fed_funds','fed_funds_90d_change','financial_conditions','financial_conditions_90d_change'],
  evidence: ['period','releaseKey','publishedAt','verifiedThrough','source'],
  jobs: ['market','status','total','updated','failed','created_at','updated_at','retained','fallback'],
};
export const markets = {US:89,TW:80};
const factors = ['growth','acceleration','operating','cashflow','value'];
const series = ['CPIAUCSL','FEDFUNDS','DFII10','NFCI'];
const macroCategories = ['rates','conditions','inflation'];
const primitive = x => x == null || ['string','number','boolean'].includes(typeof x);
const pick = (value, keys) => Object.fromEntries(keys.filter(k=>value?.[k]!==undefined&&primitive(value[k])).map(k=>[k,value[k]]));
const fail = message => { throw new Error(message); };
const digest = value => createHash('sha256').update(value).digest('hex');
const isDate = x => /^\d{4}-\d{2}-\d{2}$/.test(x);
const dateString = x => typeof x==='string'&&Number.isFinite(Date.parse(x));
const strings = a => Array.isArray(a)?a.filter(x=>typeof x==='string').slice(0,30).map(x=>x.slice(0,600)):[];
const reasons = x => ({...pick(x,publicFields.evaluation),reasons:strings(x?.reasons),fundamentalReasons:strings(x?.fundamentalReasons)});
function stockView(i) {
  const r=i.researchRank;
  return {...pick(i,publicFields.stock),
    financial:{...pick(i.financial,publicFields.finance),...(i.financial?.filingEvidence?{filingEvidence:pick(i.financial.filingEvidence,publicFields.evidence)}:{})},
    features:pick(i.features,publicFields.features),
    history:(i.history||[]).slice(-90).map(x=>pick(x,['date','close'])),
    marketEvent:i.marketEvent?pick(i.marketEvent,['effectiveDate','summary','source']):null,
    priceValidation:pick(i.priceValidation,['cutoff','validatedAt','mode']),
    evaluation:reasons(i.evaluation),
    researchRank:r?{...pick(r,['rank','score','date','archived','divergence']),detail:{
      ...pick(r.detail,['financialPeriod','filingDate','financialObservedAt','poolSize','industrySize']),
      parts:(r.detail?.parts||[]).filter(p=>factors.includes(p.key)).map(p=>pick(p,['key','weight','value','percentile']))
    }}:null};
}
function macroView(m) {
  return {...pick(m,publicFields.macro),
    categoryStates:pick(m.categoryStates,macroCategories),
    scoreRange:Array.isArray(m.scoreRange)?m.scoreRange.slice(0,2):null,
    seriesMeta:Object.fromEntries(series.map(k=>[k,pick(m.seriesMeta?.[k],['observationDate','vintageDate','publicationAt','availableBy','acquiredAt'])])),
    warning:m.status==='cached_after_failure'?'最近一次總經更新失敗。顯示最後成功資料。':m.status==='macro_incomplete'?'部分總經指標缺值，依三態規則判定。':null};
}
export function validate(snapshot) {
  if(snapshot.schema!==1||snapshot.historyThrough!=='2026-11-04'||!dateString(snapshot.generatedAt))fail('研究資料版本或日期無效');
  for(const [market,count] of Object.entries(markets)){
    const {stocks,macro}=snapshot.markets[market];
    if(stocks.length!==count||new Set(stocks.map(x=>x.symbol)).size!==count||!isDate(macro.marketAsOf)||
       !macro.marketComplete||!dateString(macro.lastMarketPublishedAt)||macro.ruleVersion!=='grouped-2of3-20261111-tristate')fail(market+' 市場快照不完整');
    const lights=macroCategories.map(k=>macro.categoryStates[k]);
    const low=lights.filter(x=>x===1).length, high=low+lights.filter(x=>x===null).length;
    if(!lights.every(x=>[0,1,null].includes(x))||JSON.stringify(macro.scoreRange)!==JSON.stringify([low,high])||
        macro.decision!==(low>=2?'red':high<2?'non_red':'unknown'))fail(market+' 資金燈號不一致');
    const candidates=stocks.filter(x=>x.researchRank).sort((a,b)=>a.researchRank.rank-b.researchRank.rank);
    for(const [index,i] of candidates.entries()){
      const r=i.researchRank,p=r.detail.parts;
      if(r.rank!==index+1||r.date!==macro.marketAsOf||i.priceDate!==r.date||r.archived||!i.integrity||i.sourceError||
         !Number.isFinite(r.score)||p.length!==(market==='US'?5:3)||new Set(p.map(z=>z.key)).size!==p.length||
         !p.every(x=>[x.weight,x.percentile,x.value].every(Number.isFinite))||
         Math.abs(r.score-p.reduce((s,x)=>s+x.weight*x.percentile*100,0))>1e-8)fail(market+' 候選分數或日期不一致 '+i.symbol);
    }
  }
  return snapshot;
}
export function project(dashboard, baseline, note='BANK-QA-A') {
  if(dashboard.authenticated!==false||!dashboard.storageReady)fail('來源必須為已發佈的公開研究快照');
  const snapshot={schema:1,sourceVersion:29,historyThrough:baseline.date,generatedAt:dashboard.serverTime,qa:note,markets:{},
    jobs:(dashboard.jobs||[]).filter(j=>Object.keys(markets).includes(j.market)).slice(0,16).map(j=>pick(j,publicFields.jobs))};
  for(const market of Object.keys(markets)){
    const stocks=dashboard.instruments.filter(x=>x.market===market).map(stockView);
    const macro=macroView(dashboard.macros[market]);
    const byYear=Object.groupBy(baseline[market],x=>x.year);
    const marks=Object.fromEntries(Object.entries(byYear).map(([year,rows])=>[year,estimatePortfolios(rows,stocks,baseline.date,macro.marketAsOf)]));
    snapshot.markets[market]={stocks,macro,marks,signalAt:dashboard.strategySignalTimes?.[market]||null};
  }
  return validate(snapshot);
}
export function envelope(snapshot) {
  validate(snapshot);
  const chunks=[];
  const raw=JSON.stringify(snapshot);
  const encoded=gzipSync(raw).toString('base64');
  for(let i=0;i<encoded.length;i+=18000)chunks.push(encoded.slice(i,i+18000));
  const checksum=digest(raw);
  const id='v29-'+digest(JSON.stringify([snapshot.markets,snapshot.jobs])).slice(0,16);
  return {id,checksum,raw,rows:[['batch_id','part','parts','sha256','payload_json'],
     ...chunks.map((part,i)=>[id,i+1,chunks.length,checksum,part])]};
}
if(process.argv[1]&&new URL(import.meta.url).pathname===process.argv[1]){
  const [input, baselineFile, snapshotFile, sheetFile]=process.argv.slice(2);
  const s=project(JSON.parse(await readFile(input)),JSON.parse(await readFile(baselineFile)));
  await writeFile(snapshotFile,JSON.stringify(s)+'\n');
  if(sheetFile)await writeFile(sheetFile,JSON.stringify(envelope(s).rows)+'\n');
}
