import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFile,mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {spawnSync} from 'node:child_process';
import {monitoredFetch} from '../engine/update-diagnostics.mjs';
import {latestClosedDate,finmind} from '../engine/providers.ts';
import {evaluate} from '../engine/rules.ts';
import {envelope} from '../engine/project.mjs';
const writer=await readFile(new URL('../apps-script/Writer.gs',import.meta.url),'utf8');
const workflow=await readFile(new URL('../.github/workflows/research-update.yml',import.meta.url),'utf8');
test('manual research backup cannot duplicate the Google-triggered daily writer',()=>{
 assert.match(workflow,/  workflow_dispatch:/);
 assert.doesNotMatch(workflow,/^  schedule:/m);
 assert.doesNotMatch(workflow,/^  push:/m);
 assert.ok(workflow.includes('group: research-publish'));
 assert.ok(workflow.includes('cancel-in-progress: false'));
});
test('US cutoff handles winter and summer time',()=>{
 assert.equal(latestClosedDate('US',new Date('2026-11-19T22:17:00+08:00')),'2026-11-18');
 assert.equal(latestClosedDate('US',new Date('2026-07-15T06:17:00+08:00')),'2026-07-14');
});
test('429 opens a host circuit, respects Retry-After and never calls same host again',async()=>{
 const old=globalThis.fetch;let calls=0;const errors=[];
 globalThis.fetch=async()=>{calls++;return new Response('limited',{status:429,headers:{'Retry-After':'7200'}})};
 try{
  await monitoredFetch('https://limited.example/one',{},(code)=>errors.push(code));
  await assert.rejects(monitoredFetch('https://limited.example/two',{},(code)=>errors.push(code)),/冷卻/);
  assert.equal(calls,1);assert.ok(errors.includes(429));
  const until=errors.find(c=>String(c).startsWith('RATE_LIMIT_UNTIL:')).slice(17);
  assert.ok(Date.parse(until)>Date.now()+7190000);
 }finally{globalThis.fetch=old;}
});
function gas(state,release){
 let http=0,reads=0,writes=0;
 const context={console,Date,LockService:{getScriptLock:()=>({tryLock:()=>true,releaseLock(){}})},
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>state[k]??null,setProperty:(k,v)=>state[k]=v,setProperties:o=>Object.assign(state,o)})},
 UrlFetchApp:{fetch:()=>{http++;return {getResponseCode:()=>200,getHeaders:()=>({}),getContentText:()=>JSON.stringify(http===1?{batch:'v29-aaaaaaaaaaaaaaaa'}:release)}}},
 Sheets:{Spreadsheets:{Values:{batchGet:()=>{reads++;throw Error('unexpected Sheets read')}},batchUpdate:()=>{writes++;throw Error('unexpected Sheets write')}}}};
 vm.createContext(context);vm.runInContext(writer,context);context.syncBankResearch();return {http,reads,writes};
}
test('five-minute unchanged revision does one static metadata GET and zero Sheets operations',()=>{
 assert.deepEqual(gas({LAST_RESEARCH_BATCH:'v29-aaaaaaaaaaaaaaaa'}),{http:1,reads:0,writes:0});
});
test('same successful batch across repeated checks never writes Sheets',()=>{
 assert.deepEqual(gas({LAST_RESEARCH_BATCH:'v29-aaaaaaaaaaaaaaaa'},{attempt:{},rows:[[],['v29-aaaaaaaaaaaaaaaa']]}),{http:1,reads:0,writes:0});
});
test('same published close skips every market API and research replay before context loading',async()=>{
 const base=await mkdtemp(join(tmpdir(),'research-no-fetch-'));
 try{
  const seed=spawnSync(process.execPath,['engine/seed.mjs',join(base,'prior.json')],{cwd:new URL('..',import.meta.url),encoding:'utf8'});
  assert.equal(seed.status,0,seed.stderr);
  const prior=JSON.parse(await readFile(join(base,'prior.json'),'utf8'));
  const market='US',block=prior.snapshot.markets.US;
  const clock=prior.snapshot.generatedAt;
  Object.assign(block.macro,{dataBasis:'RESEARCH_ESTIMATED',cpi_yoy:.03,status:'vintage_verified',
    macro_stress:0,legacyStress:0,categoryStates:{rates:0,conditions:0,inflation:0},scoreRange:[0,0]});
  block.stocks=block.stocks.map(s=>({...s,evaluation:evaluate(s,block.macro,new Date(clock))}));
  prior.rows=envelope(prior.snapshot).rows;
  await writeFile(join(base,'prior.json'),JSON.stringify(prior));
  await writeFile(join(base,'clock.mjs'),`const OriginalDate=Date;globalThis.Date=class extends OriginalDate {constructor(...a){super(...(a.length?a:[${JSON.stringify(clock)}]));}static now(){return +new OriginalDate(${JSON.stringify(clock)})}};globalThis.fetch=()=>{throw Error('NETWORK_FORBIDDEN_IN_SAME_CLOSE_TEST')};`);
  const run=()=>spawnSync(process.execPath,['--import',join(base,'clock.mjs'),'engine/update.mjs',market,join(base,'prior.json'),join(base,'out.json')],{cwd:new URL('..',import.meta.url),encoding:'utf8'});
  let result=run();assert.equal(result.status,0,result.stderr);
  let output=JSON.parse(await readFile(join(base,'out.json'),'utf8'));
  assert.equal(output.attempt.status,'same_close',output.attempt.detail);
  assert.deepEqual(output.attempt.errors,[]);assert.deepEqual(output.rows,prior.rows);
  prior.attempts={US:{market:'US',status:'failed',rateLimitedUntil:'2026-11-12T04:00:00Z'}};
  await writeFile(join(base,'prior.json'),JSON.stringify(prior));
  result=run();assert.equal(result.status,0,result.stderr);
  output=JSON.parse(await readFile(join(base,'out.json'),'utf8'));
  assert.equal(output.attempt.status,'failed');assert.match(output.attempt.detail,/冷卻/);
  assert.equal(output.attempt.rateLimitedUntil,'2026-11-12T04:00:00Z');
  assert.ok(!output.attempt.detail.includes('NETWORK_FORBIDDEN'));
 }finally{await rm(base,{recursive:true,force:true});}
});

test('FinMind JSON quota status stops subsequent calls even when HTTP status is 200',async()=>{
 const old=globalThis.fetch;let calls=0;const errors=[];
 globalThis.fetch=async()=>{calls++;return new Response(JSON.stringify({status:402,msg:'Requests reach the upper limit'}),{status:200});};
 try{
  await assert.rejects(finmind('TaiwanStockPrice','2330','2026-11-01','2026-11-19',(c)=>errors.push(c)),/配額/);
  await assert.rejects(finmind('TaiwanStockPrice','2454','2026-11-01','2026-11-19',(c)=>errors.push(c)),/冷卻/);
  assert.equal(calls,1);assert.ok(errors.includes(429));
 }finally{globalThis.fetch=old;}
});
