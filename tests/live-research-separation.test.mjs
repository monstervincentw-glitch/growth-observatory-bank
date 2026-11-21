import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {verifyMarketInputs,requirePublishable} from '../engine/publication.mjs';
import {archiveResearch} from '../engine/replay-export.mjs';

const oldFinancial={
  financial_status:'ok',latest_period:'2026-06-30',fundamental_release_key:'old',
  revenue_growth:.20,quarterly_growth:.08,operating_margin:.17,cfo_margin:.13,revenue_ttm:100
};
const newFinancial={...oldFinancial,latest_period:'2026-09-30',fundamental_release_key:'new'};
const old=[{key:'TW:2330',market:'TW',symbol:'2330',financial:oldFinancial}];
const observed={
  key:'TW:2330',market:'TW',symbol:'2330',financial:newFinancial,
  evaluation:{filingVerified:false},observedAt:'2026-11-13T08:22:22Z',
  financialObservedAt:'2026-11-13T08:22:22Z'
};

test('newly fetched TW financials can publish live without claiming filing verification',()=>{
  const result=verifyMarketInputs([observed],old);
  assert.equal(result.errors.length,0);
  assert.equal(result.warnings[0].code,'LIVE_ONLY_NOT_PIT_VERIFIED');
  assert.equal(result.counts.filingPending,1);
  assert.doesNotThrow(()=>requirePublishable(result));
});
test('new live financial version must have genuine first-observed timestamp',()=>{
  const result=verifyMarketInputs([{...observed,financialObservedAt:undefined}],old);
  assert.equal(result.errors[0].code,'INVALID_LIVE_OBSERVATION_TIME');
  assert.throws(()=>requirePublishable(result));
});
test('financial period rollback stays fatal',()=>{
  const result=verifyMarketInputs([observed],[{
    ...old[0],financial:{...oldFinancial,latest_period:'2026-12-31'}
  }]);
  assert.equal(result.errors[0].code,'FINANCIAL_ROLLBACK');
  assert.throws(()=>requirePublishable(result));
});
test('live research publication does not become historical replay input',async()=>{
  const base=await mkdtemp(join(tmpdir(),'tw-live-research-test-'));
  try{
    const context={cutoff:'2026-11-13',createdAt:'2026-11-13T08:22:22Z',
      benchmarks:{},marketDates:['2026-11-13'],
      macro:{status:'macro_incomplete',decision:'unknown'}};
    await archiveResearch(base,{market:'TW',context,batch:'test',
      bars:{2330:[]},instruments:[observed],
      attempt:{status:'published',runId:'test',errors:[]}});
    const path=join(base,'unverified-primary-inputs','TW','2026-11-13.json');
    const result=JSON.parse(await readFile(path,'utf8'));
    assert.equal(result.status,'LIVE_RESEARCH_ONLY');
    await assert.rejects(readFile(join(base,'primary-inputs','TW','2026-11-13.json')));
    await archiveResearch(base,{market:'US',context,batch:'test',
      bars:{},instruments:[{...observed,market:'US'}],
      attempt:{status:'published',runId:'test',errors:[]}});
    const us=JSON.parse(await readFile(join(base,'primary-inputs','US','2026-11-13.json'),'utf8'));
    assert.equal(us.status,'RESEARCH_VERIFIED');
    const liveContext={...context,cutoff:'2026-11-16',macro:{...context.macro,liveMacroPolicy:'post-release-20261121'}};
    await archiveResearch(base,{market:'US',context:liveContext,batch:'live',bars:{},
      instruments:[{...observed,market:'US'}],attempt:{status:'published',runId:'live',errors:[]}});
    const live=JSON.parse(await readFile(join(base,'unverified-primary-inputs','US','2026-11-16.json'),'utf8'));
    assert.equal(live.status,'LIVE_RESEARCH_ONLY');
    await assert.rejects(readFile(join(base,'primary-inputs','US','2026-11-16.json')));
  }finally{await rm(base,{recursive:true,force:true})}
});
