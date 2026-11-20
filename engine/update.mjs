import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {makeContext,refreshInstrument,latestClosedDate} from './providers.ts';
import {evaluate,rankInstruments} from './rules.ts';
import {project,envelope,validate,markets,macroView} from './project.mjs';
import {createDiagnostics} from './update-diagnostics.mjs';
import {requirePublishable,verifyMarketInputs} from './publication.mjs';
import {archiveResearch} from './replay-export.mjs';
import {archiveObservations,priorFinancialVersions} from './market-observations.mjs';

const [market, priorPath, outputPath] = process.argv.slice(2);
if(!(market in markets)||!priorPath||!outputPath)throw Error('使用方式：node engine/update.mjs US|TW 舊發布.json 新發布.json');
const baseline=JSON.parse(await readFile(new URL('../data/tracking-baseline.json',import.meta.url)));
const prior=JSON.parse(await readFile(priorPath));
const old=validate(prior.snapshot);
const clock=()=>new Date().toISOString();
const runId=process.env.GITHUB_RUN_ID
  ? `${process.env.GITHUB_RUN_ID}:${process.env.GITHUB_RUN_ATTEMPT||'1'}:${market}` : randomUUID();
const attempt={at:clock(),runId,market,status:'started',detail:'',expected:markets[market],updated:0,failed:0};
const diagnostics=createDiagnostics(clock,runId);
const affected=new Set();
const financialFailed=new Set();
const report=(type,symbol)=> (code,provider)=>diagnostics.record({type,symbol},code,provider);
let marketWide=false;
let snapshot=old;
let replayResearch;
let replayContext;
let observations;

try{
  const before=old.markets[market];
  const previous=prior.attempts?.[market] ?? (prior.attempt?.market===market?prior.attempt:null);
  if(previous?.rateLimitedUntil && previous.rateLimitedUntil>clock()){
    attempt.rateLimitedUntil=previous.rateLimitedUntil;
    throw Error('來源限流冷卻至 '+previous.rateLimitedUntil+'；本次不呼叫行情 API');
  }
  const alreadyPublished=latestClosedDate(market)===before.macro.marketAsOf &&
    before.macro.dataBasis && before.macro.cpi_yoy!==null && before.macro.status==='vintage_verified';
  if(alreadyPublished && process.env.RESEARCH_FORCE_MACRO_REFRESH!=='1'){
    attempt.status='same_close';
    attempt.detail='同一收盤日已完整發布；未呼叫股價、財報、FRED/ALFRED，也未重算研究池。';
  }else{
  const context=await makeContext(market,undefined,false,report,true);
  if(before.macro.poolCheck)context.macro.poolCheck=before.macro.poolCheck;
  replayContext=context;
  if(context.cutoff<before.macro.marketAsOf)throw Error('基準行情日期倒退，保留前批');
  if(!['vintage_verified','macro_incomplete'].includes(context.macro.status)||
     !context.macro.categoryStates||Object.values(context.macro.categoryStates).every(x=>x===null))
    throw Error('總經來源或版本驗證失敗');
  if(context.cutoff===before.macro.marketAsOf){
    // Re-evaluate repaired macro inputs even when quotes are already published.
    const fields=['categoryStates','cpi_yoy','cpi_3m_annualized','real_yield','fed_funds','financial_conditions','dataBasis'];
    const changed=fields.some(k=>JSON.stringify(context.macro[k]??null)!==JSON.stringify(before.macro[k]??null));
    if(changed){
      const now=clock();
      snapshot=structuredClone(old);
      const block=snapshot.markets[market];
      block.macro=macroView({...context.macro,marketComplete:true,lastMarketPublishedAt:now});
      block.stocks=block.stocks.map(stock=>({...stock,evaluation:evaluate(stock,block.macro)}));
      block.signalAt=now;snapshot.generatedAt=now;
      validate(snapshot);
      attempt.status='published';attempt.updated=markets[market];
      attempt.detail='同一收盤日重新核對總經與通膨研究補值；行情與排名保留已驗證資料。';
    }else{
      attempt.status='same_close';
      attempt.detail='最近完整收盤已發布；休市或同日重跑。';
    }
  }else{
    const stocks=[];
    const bars={};
    const errors=[];
    observations={market,context,stocks,previous:before.stocks,bars,financialAudit:{}};
    for(let n=0;n<before.stocks.length;n+=3){
      const group=before.stocks.slice(n,n+3);
      const results=await Promise.allSettled(group.map(stock=>refreshInstrument(stock,context,false,[],type=>report(type,stock.symbol),market==='TW')));
      for(const [i,result] of results.entries()){
        if(result.status==='fulfilled'){
          const {_bars,_financialAudit,...data}=result.value;
          if(_financialAudit)observations.financialAudit[data.symbol]=_financialAudit;
          stocks.push(data);
          if(_bars)bars[data.symbol]=_bars;
          attempt.updated++;
          if(data._financialError){
            affected.add(data.symbol);
            financialFailed.add(data.symbol);
            report('financial',data.symbol)('FINANCIAL_SOURCE_ERROR','FinMind');
          }
        }
        else {
          const symbol=group[i].symbol, type=result.reason?.dataType||'quote';
          affected.add(symbol);
          if(!diagnostics.errors.some(e=>e.symbol===symbol&&e.type===type)) report(type,symbol)('STOCK_DATA_FAILED');
          errors.push(symbol+': '+String(result.reason?.message||result.reason).slice(0,200));
        }
      }
      if(diagnostics.errors.some(e=>[429,403].includes(e.code)))throw Error('來源限流或拒絕存取；停止本批後續請求，保留前批');
    }
    if(errors.length)throw Error('部分標的未通過驗證，保留全批：'+errors.join('｜'));
    const now=clock();
    const macro={...context.macro,marketComplete:true,lastMarketPublishedAt:now};
    const scores=rankInstruments(stocks,macro,context.cutoff).filter(x=>x.rankScore!==null)
      .sort((a,b)=>b.rankScore-a.rankScore||a.key.localeCompare(b.key));
    const ranks=new Map(scores.map((s,i)=>[s.key,{rank:i+1,score:s.rankScore,detail:s.rankDetail,date:context.cutoff,archived:false,divergence:evaluate(s,macro).divergence}]));
    const instruments=stocks.map(s=>({...s,evaluation:evaluate(s,macro),researchRank:ranks.get(s.key)||null}));
    const financialReview=verifyMarketInputs(instruments,before.stocks);
    attempt.health={...financialReview.counts,
      liveOnlyFinance:financialReview.warnings.length,macro:macro.status};
    attempt.publicationBasis=market==='TW'&&financialReview.counts.filingPending>0
      ?'LIVE_FIRST_OBSERVED_NOT_PIT':'ORIGINAL_VERIFIED_RULES';
    for (const {symbol,code} of financialReview.errors) {
      affected.add(symbol);
      financialFailed.add(symbol);
      report('financial',symbol)(code);
    }
    replayResearch={market,context,bars,instruments};
    requirePublishable(financialReview);
    if(financialFailed.size)throw Error('財報來源未驗證，保留上一個正式策略批次');
    const newJob={market,status:'done',total:markets[market],updated:attempt.updated,failed:0,created_at:attempt.at,updated_at:now,retained:0,
      fallback:stocks.filter(s=>s.priceValidation?.mode==='fallback_verified').length};
    const dashboard={authenticated:false,storageReady:true,serverTime:now,jobs:[newJob,...old.jobs],
      instruments:[...instruments,...Object.entries(old.markets).filter(([k])=>k!==market).flatMap(([,v])=>v.stocks)],
      macros:{...Object.fromEntries(Object.entries(old.markets).map(([k,v])=>[k,v.macro])),[market]:macro},
      strategySignalTimes:{...Object.fromEntries(Object.entries(old.markets).map(([k,v])=>[k,v.signalAt])),[market]:now}};
    snapshot=project(dashboard,baseline,old.qa);
    attempt.status='published';
    attempt.detail=(attempt.publicationBasis==='LIVE_FIRST_OBSERVED_NOT_PIT'
      ?'已發布即時研究與收盤行情；新財報自首次取得後可供研究，尚未證明收盤前可得，不得直接延伸歷史回測。'
      :'收盤已驗證並發布排名。')+'財報及總經狀態：'+JSON.stringify(attempt.health);
  }
  }
}catch(error){
  attempt.status='failed';
  marketWide=affected.size===0;
  if(marketWide&&!diagnostics.errors.some(e=>e.type==='macro'||e.type==='benchmark')) report('publication',null)('MARKET_DATA_FAILED');
  attempt.detail=String(error.message||error).slice(0,1600);
}
const {id,rows}=snapshot===old && prior.rows ? {id:prior.rows[1][0],rows:prior.rows} : envelope(snapshot);
attempt.batch=id;
attempt.finishedAt=clock();
attempt.failed=affected.size;
attempt.succeeded=attempt.updated-financialFailed.size;
attempt.notAttempted=attempt.expected-attempt.succeeded-attempt.failed;
attempt.updated=attempt.succeeded;
attempt.marketWide=marketWide;
attempt.affected=[...affected].sort();
const cooldowns=diagnostics.errors.filter(e=>String(e.code).startsWith('RATE_LIMIT_UNTIL:')).map(e=>String(e.code).slice(17));
if(cooldowns.length)attempt.rateLimitedUntil=cooldowns.sort().at(-1);
attempt.errors=diagnostics.errors.map(error=>({...error,batch:id}));
const previousAttempt=prior.attempts?.[market]??(prior.attempt?.market===market?prior.attempt:null);
if(attempt.status==='same_close'){
  const previousFailure=['failed','quotes_only'].includes(previousAttempt?.status)?previousAttempt:previousAttempt?.unresolvedFailure;
  if(previousFailure) attempt.unresolvedFailure=previousFailure;
}
attempt.us_close=snapshot.markets.US.macro.marketAsOf;
attempt.tw_close=snapshot.markets.TW.macro.marketAsOf;
attempt.lastSuccessDate=snapshot.markets[market].macro.marketAsOf;
attempt.lastSuccessAt=snapshot.markets[market].macro.lastMarketPublishedAt;
attempt.detail += `｜預期 ${attempt.expected}，成功 ${attempt.succeeded}，失敗 ${attempt.failed}，未處理 ${attempt.notAttempted}｜最後成功收盤 ${attempt.lastSuccessDate}`;
const archiveDir=process.env.PERFORMANCE_INPUT_DIR;
if(archiveDir&&observations){
  try{
    const registry=await priorFinancialVersions(process.env.FINANCIAL_VERSION_INDEX);
    const info=await archiveObservations(archiveDir,{...observations,registry,batch:id,attempt});
    Object.assign(attempt,info);
    if(market==='TW'&&attempt.status==='failed'&&info.quoteCount===attempt.expected&&financialFailed.size===attempt.failed&&attempt.failed>0)
      attempt.status='quotes_only';
  }catch(error){
      // A failed archive is a blocking provenance failure for TW replay:
      // previously this warning was ignored and entire trading dates vanished.
      if(market==='TW')throw error;
      console.warn('Market observation archive: '+String(error.message||error));
    }
}
const priorAttempts=prior.attempts??(prior.attempt?.market?{[prior.attempt.market]:prior.attempt}:{});
await writeFile(outputPath,JSON.stringify({snapshot,rows,attempt,attempts:{...priorAttempts,[market]:attempt}})+'\n');
console.log(JSON.stringify(attempt));

if(archiveDir&&replayResearch){
  try {await archiveResearch(archiveDir,{...replayResearch,batch:id,attempt});}
  catch(error){console.warn('Primary replay input: '+String(error.message||error));}
}

const etfContext=process.env.PERFORMANCE_ETF_CONTEXT;
if(etfContext&&market==='US'&&replayContext&&replayResearch&&attempt.status==='published'){
  try {await writeFile(etfContext,JSON.stringify({context:replayContext,batch:id})+'\n');}
  catch(error){console.warn('ETF context: '+String(error.message||error));}
}
