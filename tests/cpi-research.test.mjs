import test from 'node:test';
import assert from 'node:assert/strict';
import {interpolateCpi,CPI_SERIES} from '../engine/cpi-research.mjs';
import {scoreMacro,unavailableMacro} from '../engine/macro.ts';
import {archiveResearch} from '../engine/replay-export.mjs';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const rows=[{date:'2025-09-01',value:324.245},{date:'2025-11-01',value:325.063}];
const opts={enabled:true,vintage:'2026-11-18',acquiredAt:'2026-11-19T13:00:00Z'};
test('each CPI series uses its own neighbours without changing official rows',()=>{
 for(const series of CPI_SERIES){
  const original=structuredClone(rows), result=interpolateCpi(series,rows,opts);
  assert.deepEqual(rows,original);assert.equal(result.rows[1].value,Math.sqrt(324.245*325.063));
  assert.equal(result.estimates[0].dataStatus,'ESTIMATED_INTERPOLATED');
  assert.equal(result.estimates[0].availableAt,opts.acquiredAt);
 }
 assert.deepEqual(interpolateCpi('FEDFUNDS',rows,opts).estimates,[]);
});
test('PIT default, unavailable future neighbours and official observations are preserved',()=>{
 for(const options of [{...opts,enabled:false},{...opts,vintage:'2025-11-30'},{...opts,acquiredAt:'2025-12-17T12:00:00Z'}])
  assert.deepEqual(interpolateCpi('CPIAUCSL',rows,options).estimates,[]);
 assert.deepEqual(interpolateCpi('CPIAUCSL',[...rows,{date:'2025-10-01',value:324}],opts).estimates,[]);
 assert.deepEqual(interpolateCpi('CPIAUCSL',rows.slice(0,1),opts).estimates,[]);
});
test('missing October no longer blocks live YoY; historical mode still reports missing',()=>{
 const vintage='2026-11-18',raw={};
 const data={CPIAUCSL:[...rows,{date:'2026-07-01',value:333},{date:'2026-10-01',value:337}],
 FEDFUNDS:[{date:'2026-06-01',value:3.5},{date:'2026-10-01',value:3.88}],
 DFII10:[{date:'2026-08-01',value:2.4},{date:'2026-11-16',value:2.92}],
 NFCI:[{date:'2026-08-01',value:-.5},{date:'2026-11-06',value:-.462}]};
 for(const [s,rs] of Object.entries(data))raw[s]='observation_date,'+s+'_20261118\n'+rs.map(r=>r.date+','+r.value).join('\n');
 const historical=unavailableMacro('2026-11-19',vintage,raw,null,new Date(opts.acquiredAt),vintage);
 assert.equal(historical.cpi_yoy,null);
 const live=scoreMacro('2026-11-19',vintage,raw,new Date(opts.acquiredAt),vintage,true);
 assert.ok(Number.isFinite(live.cpi_yoy));assert.equal(live.categoryStates.inflation,0);
 assert.equal(live.dataBasis,'RESEARCH_ESTIMATED');assert.equal(live.researchEstimates.length,1);
});
test('estimated live research never creates verified primary replay inputs',async()=>{
 const folder=await mkdtemp(join(tmpdir(),'cpi-pit-'));
 try{
  for(const market of ['US','TW']){
   await archiveResearch(folder,{market,context:{cutoff:'2026-11-18',createdAt:opts.acquiredAt,macro:{researchEstimates:[{}],status:'vintage_verified'},benchmarks:{QQQ:[]},marketDates:[]},batch:'test',bars:{},instruments:[],attempt:{status:'published'}});
   const result=JSON.parse(await readFile(join(folder,'unverified-primary-inputs',market,'2026-11-18.json'),'utf8'));
   assert.equal(result.status,'LIVE_RESEARCH_ONLY');
   await assert.rejects(readFile(join(folder,'primary-inputs',market,'2026-11-18.json')));
  }
 }finally{await rm(folder,{recursive:true,force:true});}
});
