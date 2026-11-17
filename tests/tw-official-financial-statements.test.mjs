import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, readFile,writeFile, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {parseOfficialStatements,archiveOfficialStatements,OFFICIAL_STATEMENTS} from '../engine/archive-tw-financial-statements.mjs';

const stock=(i,extra={})=>({
  '出表日期':'1151117','年度':'115','季別':'3','公司代號':String(2000+i),
  '公司名稱':'假資料','營業收入':'100000.00','營業利益（損失）':'9000.00',...extra,
});
const tracked=Array.from({length:80},(_,i)=>String(2000+i));

test('TWSE official financial values are captured, but not mistreated as original filing time',()=>{
 const result=parseOfficialStatements([stock(0),stock(1)],tracked,'2026-11-17T03:25:31.000Z');
 assert.equal(result.reportDate,'2026-11-17');
 assert.equal(result.represented,2);
 assert.equal(result.missing.length,78);
 const r=result.rows[0];
 assert.equal(r.period,'2026-09-30');
 assert.equal(r.sourcePrintDate,'2026-11-17');
 assert.equal(r.firstObservedAt,'2026-11-17T03:25:31.000Z');
 assert.equal(r.officialIndividualFilingTime,null);
 assert.equal(r.matchesFinMindVersion,false);
 assert.equal(r.eligibleForHistoricalPointInTimeBacktest,false);
 assert.equal(r.sourceUrl,OFFICIAL_STATEMENTS);
});

test('reject malformed figures, periods and mixed print dates',()=>{
 for(const bad of [
  {'季別':'5'},{'營業收入':'foo'},{'營業利益（損失）':''},
  {'出表日期':'1151132'}
 ])assert.throws(()=>parseOfficialStatements([stock(0,bad)],tracked,'2026-11-17T00:00:00.000Z'));
 assert.throws(()=>parseOfficialStatements([stock(0),stock(0)],tracked,'2026-11-17T00:00:00.000Z'));
 assert.throws(()=>parseOfficialStatements([stock(0),stock(1,{'出表日期':'1151116'})],tracked,'2026-11-17T00:00:00.000Z'));
});

test('archive stores source response and same-day rechecks idempotently, never upgrades trust',async()=>{
 const root=await mkdtemp(join(tmpdir(),'tw-financial-report-'));
 try{
  await writeFile(join(root,'registry.json'),JSON.stringify(Object.fromEntries(tracked.map(s=>[s,{}]))));
  const body=JSON.stringify([stock(0),stock(1)]);
  const fetcher=async url=>{assert.equal(url,OFFICIAL_STATEMENTS);return {ok:true,text:async()=>body};};
  const clock=()=>new Date('2026-11-17T03:25:31.000Z');
  await archiveOfficialStatements(join(root,'registry.json'),join(root,'proof'),{fetcher,clock});
  await archiveOfficialStatements(join(root,'registry.json'),join(root,'proof'),{fetcher,clock});
  const stored=JSON.parse(await readFile(join(root,'proof','financial-statements','2026-11-17.json'),'utf8'));
  assert.equal(stored.observations.length,1);
  assert.equal(stored.observations[0].responseRows,2);
  assert.equal(stored.observations[0].matchingStocks,2);
  assert.equal(stored.observations[0].data[0].eligibleForHistoricalPointInTimeBacktest,false);
  assert.equal(stored.observations[0].missingStocks.length,78);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('failure of official endpoint does not create a false evidence snapshot',async()=>{
 const root=await mkdtemp(join(tmpdir(),'tw-financial-failure-'));
 try{
  await writeFile(join(root,'registry.json'),JSON.stringify(Object.fromEntries(tracked.map(s=>[s,{}]))));
  await assert.rejects(archiveOfficialStatements(join(root,'registry.json'),join(root,'proof'),{
   fetcher:async()=>({ok:false,status:429}),
  }),/HTTP 429/);
  await assert.rejects(readFile(join(root,'proof','financial-statements','2026-11-17.json'))); 
 }finally{await rm(root,{recursive:true,force:true});}
});
