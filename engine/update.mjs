import {randomUUID} from 'node:crypto';
import {readFile,writeFile} from 'node:fs/promises';
import {makeContext,refreshInstrument} from './providers.ts';
import {evaluate,rankInstruments} from './rules.ts';
import {project,envelope,validate,markets} from './project.mjs';
import {createDiagnostics} from './update-diagnostics.mjs';
import {requirePublishable,verifyMarketInputs} from './publication.mjs';

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

try{
  const context=await makeContext(market,undefined,false,report);
  const before=old.markets[market];
  if(context.cutoff<before.macro.marketAsOf)throw Error('基準行情日期倒退，保留前批');
  if(!['vintage_verified','macro_incomplete'].includes(context.macro.status)||
     !context.macro.categoryStates||Object.values(context.macro.categoryStates).every(x=>x===null))
    throw Error('總經來源或版本驗證失敗');
  if(context.cutoff===before.macro.marketAsOf){
    attempt.status='same_close';
    attempt.detail='最近完整收盤已發布；休市或同日重跑。';
  }else{
    const stocks=[];
    const errors=[];
    for(let n=0;n<before.stocks.length;n+=3){
      const group=before.stocks.slice(n,n+3);
      const results=await Promise.allSettled(group.map(stock=>refreshInstrument(stock,context,false,[],type=>report(type,stock.symbol))));
      for(const [i,result] of results.entries()){
        if(result.status==='fulfilled'){const {_bars,...data}=result.value;stocks.push(data);attempt.updated++;}
        else {
          const symbol=group[i].symbol, type=result.reason?.dataType||'quote';
          affected.add(symbol);
          if(!diagnostics.errors.some(e=>e.symbol===symbol&&e.type===type)) report(type,symbol)('STOCK_DATA_FAILED');
          errors.push(symbol+': '+String(result.reason?.message||result.reason).slice(0,200));
        }
      }
    }
    if(errors.length)throw Error('部分標的未通過驗證，保留全批：'+errors.join('｜'));
    const now=clock();
    const macro={...context.macro,marketComplete:true,lastMarketPublishedAt:now};
    const scores=rankInstruments(stocks,macro,context.cutoff).filter(x=>x.rankScore!==null)
      .sort((a,b)=>b.rankScore-a.rankScore||a.key.localeCompare(b.key));
    const ranks=new Map(scores.map((s,i)=>[s.key,{rank:i+1,score:s.rankScore,detail:s.rankDetail,date:context.cutoff,archived:false,divergence:evaluate(s,macro).divergence}]));
    const instruments=stocks.map(s=>({...s,evaluation:evaluate(s,macro),researchRank:ranks.get(s.key)||null}));
    const financialReview=verifyMarketInputs(instruments,before.stocks);
    attempt.health={...financialReview.counts, macro:macro.status};
    for (const {symbol,code} of financialReview.errors) {
      affected.add(symbol);
      financialFailed.add(symbol);
      report('financial',symbol)(code);
    }
    requirePublishable(financialReview);
    const newJob={market,status:'done',total:markets[market],updated:attempt.updated,failed:0,created_at:attempt.at,updated_at:now,retained:0,
      fallback:stocks.filter(s=>s.priceValidation?.mode==='fallback_verified').length};
    const dashboard={authenticated:false,storageReady:true,serverTime:now,jobs:[newJob,...old.jobs],
      instruments:[...instruments,...Object.entries(old.markets).filter(([k])=>k!==market).flatMap(([,v])=>v.stocks)],
      macros:{...Object.fromEntries(Object.entries(old.markets).map(([k,v])=>[k,v.macro])),[market]:macro},
      strategySignalTimes:{...Object.fromEntries(Object.entries(old.markets).map(([k,v])=>[k,v.signalAt])),[market]:now}};
    snapshot=project(dashboard,baseline,old.qa);
    attempt.status='published';
    attempt.detail='收盤已驗證並發布排名。財報及總經狀態：'+JSON.stringify(attempt.health);
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
attempt.errors=diagnostics.errors.map(error=>({...error,batch:id}));
const previousAttempt=prior.attempts?.[market]??(prior.attempt?.market===market?prior.attempt:null);
if(attempt.status==='same_close'){
  const previousFailure=previousAttempt?.status==='failed'?previousAttempt:previousAttempt?.unresolvedFailure;
  if(previousFailure) attempt.unresolvedFailure=previousFailure;
}
attempt.us_close=snapshot.markets.US.macro.marketAsOf;
attempt.tw_close=snapshot.markets.TW.macro.marketAsOf;
attempt.lastSuccessDate=snapshot.markets[market].macro.marketAsOf;
attempt.lastSuccessAt=snapshot.markets[market].macro.lastMarketPublishedAt;
attempt.detail += `｜預期 ${attempt.expected}，成功 ${attempt.succeeded}，失敗 ${attempt.failed}，未處理 ${attempt.notAttempted}｜最後成功收盤 ${attempt.lastSuccessDate}`;
const priorAttempts=prior.attempts??(prior.attempt?.market?{[prior.attempt.market]:prior.attempt}:{});
await writeFile(outputPath,JSON.stringify({snapshot,rows,attempt,attempts:{...priorAttempts,[market]:attempt}})+'\n');
console.log(JSON.stringify(attempt));
