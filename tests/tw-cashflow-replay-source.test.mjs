import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {archiveTwCashflow} from '../engine/tw-cashflow-replay-source.mjs';
const universe=Array.from({length:80},(_,i)=>String(2000+i));
test('full 80-stock raw cashflow evidentiary capture preserves fetch-time, never backdates',async()=>{
 const root=await mkdtemp(join(tmpdir(),'tw-replay-cash-'));
 try{
  const registry=join(root,'versions.json');
  await writeFile(registry,JSON.stringify(Object.fromEntries(universe.map(s=>[s,{}]))));
  const f=async (dataset,symbol)=>[
    {stock_id:symbol,date:'2026-03-31',type:'CashFlowsFromOperatingActivities',value:123},
    {stock_id:symbol,date:'2026-06-30',type:'CashFlowsFromOperatingActivities',value:234},
    {stock_id:symbol,date:'2026-09-30',type:'CashFlowsFromOperatingActivities',value:345}
  ];
  const out=await archiveTwCashflow(registry,root,{fetcher:f,clock:()=>new Date('2026-11-17T05:00:00Z'),sleep:async()=>{}});
  assert.equal(out.status,'COMPLETE');assert.equal(out.received,80);
  assert.equal(out.originalHistoricalCapture,false);
  assert.equal(out.stocks['2000'].firstObservedAt,'2026-11-17T05:00:00.000Z');
  assert.equal(out.stocks['2000'].rows.length,3);
  assert.equal(JSON.parse(await readFile(join(root,'cashflow-reconstructed-2026-11-17.json'),'utf8')).auditOnly,true);
 }finally{await rm(root,{recursive:true,force:true});}
});
test('missing quarterly cashflow blocks verified numerical source',async()=>{
 const root=await mkdtemp(join(tmpdir(),'tw-replay-cash-partial-'));
 try{
  const registry=join(root,'versions.json');
  await writeFile(registry,JSON.stringify(Object.fromEntries(universe.map(s=>[s,{}]))));
  const f=async (dataset,symbol)=>symbol==='2000'?[]:[
    {stock_id:symbol,date:'2026-06-30',type:'CashFlowsFromOperatingActivities',value:234}];
  const out=await archiveTwCashflow(registry,root,{fetcher:f,sleep:async()=>{}});
  assert.equal(out.status,'PARTIAL');assert.equal(out.received,79);
  assert.match(out.errors['2000'],/Q2/);
  process.exitCode=0;
 }finally{await rm(root,{recursive:true,force:true});}
});
