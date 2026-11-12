import {readFile,writeFile} from 'node:fs/promises';
import {gunzipSync} from 'node:zlib';
import {makeContext,refreshInstrument} from './providers.ts';
import {evaluate,rankInstruments} from './rules.ts';
import {project,envelope,validate,markets} from './project.mjs';

const [market, priorPath, outputPath] = process.argv.slice(2);
if(!(market in markets)||!priorPath||!outputPath)throw Error('使用方式：node engine/update.mjs US|TW 舊發布.json 新發布.json');
const baseline=JSON.parse(await readFile(new URL('../data/tracking-baseline.json',import.meta.url)));
const prior=JSON.parse(await readFile(priorPath));
const old=validate(prior.snapshot);
const clock=()=>new Date().toISOString();
const attempt={at:clock(),market,status:'started',detail:'',updated:0,failed:0};
let snapshot=old;

try{
  const context=await makeContext(market,undefined,false);
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
      const results=await Promise.allSettled(group.map(stock=>refreshInstrument(stock,context,false)));
      for(const [i,result] of results.entries()){
        if(result.status==='fulfilled'){const {_bars,...data}=result.value;stocks.push(data);attempt.updated++;}
        else {attempt.failed++;errors.push(group[i].symbol+': '+String(result.reason.message||result.reason).slice(0,200));}
      }
    }
    if(errors.length)throw Error('部分標的未通過驗證，保留全批：'+errors.join('｜'));
    const now=clock();
    const macro={...context.macro,marketComplete:true,lastMarketPublishedAt:now};
    const scores=rankInstruments(stocks,macro,context.cutoff).filter(x=>x.rankScore!==null)
      .sort((a,b)=>b.rankScore-a.rankScore||a.key.localeCompare(b.key));
    const ranks=new Map(scores.map((s,i)=>[s.key,{rank:i+1,score:s.rankScore,detail:s.rankDetail,date:context.cutoff,archived:false,divergence:evaluate(s,macro).divergence}]));
    const instruments=stocks.map(s=>({...s,evaluation:evaluate(s,macro),researchRank:ranks.get(s.key)||null}));
    const newJob={market,status:'done',total:markets[market],updated:attempt.updated,failed:0,created_at:attempt.at,updated_at:now,retained:0,
      fallback:stocks.filter(s=>s.priceValidation?.mode==='fallback_verified').length};
    const dashboard={authenticated:false,storageReady:true,serverTime:now,jobs:[newJob,...old.jobs],
      instruments:[...instruments,...Object.entries(old.markets).filter(([k])=>k!==market).flatMap(([,v])=>v.stocks)],
      macros:{...Object.fromEntries(Object.entries(old.markets).map(([k,v])=>[k,v.macro])),[market]:macro},
      strategySignalTimes:{...Object.fromEntries(Object.entries(old.markets).map(([k,v])=>[k,v.signalAt])),[market]:now}};
    snapshot=project(dashboard,baseline,old.qa);
    attempt.status='published';
    attempt.detail='完整收盤、財報、總經與排名已核對。';
  }
}catch(error){
  attempt.status='failed';
  attempt.detail=String(error.message||error).slice(0,1600);
}
const {id,rows}=envelope(snapshot);
attempt.batch=id;
attempt.us_close=snapshot.markets.US.macro.marketAsOf;
attempt.tw_close=snapshot.markets.TW.macro.marketAsOf;
await writeFile(outputPath,JSON.stringify({snapshot,rows,attempt})+'\n');
console.log(JSON.stringify(attempt));
