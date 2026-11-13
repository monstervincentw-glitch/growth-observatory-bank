import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {days,evaluate,rankInstruments,verifiedFiling} from './rules.ts';
import {financialVersions} from './financial-versions.mjs';

export async function priorFinancialVersions(path) {
  if(!path)return {};
  try{return JSON.parse(await readFile(path,'utf8'));}
  catch(error){if(error.code==='ENOENT')return {};throw error;}
}

function researchRow(stock, context, version) {
  const financial=version?{...version.financial,
    financial_age_days:days(context.cutoff,version.period)}:null;
  if(!financial)return null;
  return {...stock,financial,financialObservedAt:version.firstVerifiedAt};
}

export async function archiveObservations(folder,{market,context,stocks,previous,bars,registry,batch,attempt}) {
  const at=attempt.finishedAt, day=context.cutoff;
  const close=day+(market==='TW'?'T13:30:00+08:00':'T16:00:00'+new Intl.DateTimeFormat('en-US',
    {timeZone:'America/New_York',timeZoneName:'longOffset'}).formatToParts(new Date(day+'T18:00:00Z'))
    .find(x=>x.type==='timeZoneName').value.slice(3));
  const old=new Map(previous.map(s=>[s.symbol,s]));
  const quote={}, next={...registry};
  const active=[], pending=[];
  const current=new Map(stocks.map(s=>[s.symbol,s]));
  for(const stock of stocks){
    if(stock.marketEvent||!bars[stock.symbol]?.length)continue;
    const code=stock.symbol;
    const entry=financialVersions(stock,old.get(code),registry[code],close,at);
    next[code]=entry;
    const effective=entry.versions[entry.active];
    const row=researchRow(stock,context,effective);
    if(row)active.push(row);
    const official=verifiedFiling(stock);
    if(stock._financialError||!effective||!entry.observed||(entry.active!==entry.observed&&!official))
      pending.push(code);
    quote[code]={priceDate:stock.priceDate,close:stock.close,history:stock.history,
      source:stock.priceSource,priceValidation:stock.priceValidation,
      bars:bars[code],financialVersion:entry.active,observedVersion:entry.observed,
      officialFilingVerified:official,financeError:stock._financialError??null};
  }
  const complete=Object.keys(quote).length===previous.filter(s=>!current.get(s.symbol)?.marketEvent).length;
  const rankReady=complete&&active.length===Object.keys(quote).length&&context.macro.decision!=='unknown'&&
    active.every(s=>s.financial.financial_age_days<=180)&&
    !Object.values(quote).some(q=>q.financeError);
  const decisionStatus=rankReady?'POINT_IN_TIME_INPUT_VERIFIED':'DECISION_INPUT_PENDING';
  const ranks=rankInstruments(active,context.macro,day);
  const observations=Object.fromEntries(ranks.map(s=>[s.symbol,{
    financial:s.financial,financialVersion:next[s.symbol].versions[next[s.symbol].active],
    rankScore:rankReady?s.rankScore:null,rankDetail:rankReady?s.rankDetail:null,features:s.features,
    status:pending.includes(s.symbol)?'RETAINED_PRIOR_VERSION':'VERIFIED_AT_DECISION',
    evaluation:evaluate(s,context.macro,new Date(at))
  }]));
  const output={schema:1,market,day,batch,runId:attempt.runId,observedAt:at,decisionAt:close,
    status:complete?'PRICES_VERIFIED':'PARTIAL_PRICES',decisionStatus,
    researchStatus:rankReady?'FULL_OBSERVATION_INPUT':'PARTIAL_OBSERVATION_INPUT',
    pendingFinancial:pending.sort(),macro:context.macro,quotes:quote,observations,
    expected:previous.length,inactive:stocks.filter(s=>s.marketEvent).map(s=>s.symbol)};
  if(rankReady&&market==='TW'){
    const replayStocks=Object.fromEntries(Object.entries(observations).map(([s,obs])=>[s,{...obs,
      bars:quote[s].bars,source:quote[s].source,quoteStatus:quote[s].priceValidation,
      industry:current.get(s).industry,name:current.get(s).name}]));
    const path=join(folder,'observed-primary-inputs',market);
    await mkdir(path,{recursive:true});
    await writeFile(join(path,day+'.json'),JSON.stringify({schema:1,market,cutoff:day,batch,
      status:decisionStatus,decisionAt:close,observedAt:at,macro:context.macro,stocks:replayStocks})+'\n');
  }
  const publicPrices={schema:1,market,day,observedAt:at,runId:attempt.runId,
    status:output.status,expected:previous.length,quotes:Object.fromEntries(Object.entries(quote).map(([s,q])=>
      [s,{priceDate:q.priceDate,close:q.close,source:q.source,adjustedClose:q.bars.at(-1).adjClose,
        research:observations[s] ? {status:observations[s].status,financial:observations[s].financial} : null}]))};
  for(const [path,name,value] of [
    [join(folder,'financial-versions'),market+'.json',next],
    [join(folder,'market-observations',market),day+'.json',output],
    [join(folder,'market-observations',market),'latest.json',output],
    [join(folder,'market-prices',market),'latest.json',publicPrices]
  ]){
    await mkdir(path,{recursive:true});
    await writeFile(join(path,name),JSON.stringify(value)+'\n');
  }
  return {quoteDate:day,quoteCount:Object.keys(quote).length,pendingFinancial:pending.length,
    observationStatus:output.researchStatus};
}
