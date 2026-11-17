import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,mkdir,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {
 captureFinMindIncome,compareWithOfficial,reconcileOfficialAndFinMind
} from '../engine/tw-financial-reconciliation.mjs';

const clock='2026-11-17T03:30:00.000Z';
const vals=(symbol='2330',operating=undefined)=>Array.from({length:3},(_,i)=>{
 const quarter=['2026-03-31','2026-06-30','2026-09-30'][i];
 return [
 {date:quarter,stock_id:symbol,type:'Revenue',value:[1_000_000,2_000_000,3_000_000][i],origin_name:'營業收入'},
 {date:quarter,stock_id:symbol,type:'OperatingIncome',value:operating?.[i]??[100_000,200_000,300_000][i],origin_name:'營業利益'},
 ];
}).flat();
const official=(overrides={})=>({
 symbol:'2330',period:'2026-09-30',revenue:'6000.00',operatingProfit:'600.00',
 firstObservedAt:clock,originalRowSha256:'abcd',
 sourceUrl:'https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci',...overrides,
});

test('FinMind source rows preserve single-quarter NTD amounts, provenance, and actual types',()=>{
 const r=captureFinMindIncome(vals(),'2330','2026-11-17T02:00:00Z');
 assert.equal(r.source,'FinMind TaiwanStockFinancialStatements');
 assert.equal(r.quarters.length,3);
 assert.equal(r.quarters[0].revenue,1_000_000);
 assert.equal(r.quarters[0].operatingIncome,100_000);
 assert.equal(r.quarters[0].revenueType,'Revenue');
 assert.equal(r.firstObservedAt,'2026-11-17T02:00:00Z');
 assert.match(r.sourceResponseSha256,/^[0-9a-f]{64}$/);
});

test('correctly reconcile official YTD THOUSANDS with FinMind single-quarter NTD',()=>{
 const comparison=compareWithOfficial(captureFinMindIncome(vals(),'2330',clock),official());
 assert.equal(comparison.status,'VALUES_MATCHED_NOT_FILING_TIME_VERIFIED');
 assert.deepEqual(comparison.comparedQuarters,['2026-03-31','2026-06-30','2026-09-30']);
 assert.equal(comparison.revenue.officialYtdThousands,6000);
 assert.equal(comparison.revenue.finmindSumQuarterThousands,6000);
 assert.equal(comparison.operatingIncome.finmindSumQuarterThousands,600);
 assert.equal(comparison.officialFilingTimeVerified,false);
 assert.equal(comparison.eligibleForHistoricalPointInTimeBacktest,false);
});

test('do not approve incorrect units, different values or unobserved quarters',()=>{
 const finmind=captureFinMindIncome(vals(),'2330',clock);
 assert.equal(compareWithOfficial(finmind,official({revenue:'6000000'})).status,'VALUE_MISMATCH');
 assert.equal(compareWithOfficial(finmind,official({period:'2026-12-31'})).status,'PERIOD_NOT_AVAILABLE_IN_FINMIND');
 assert.equal(compareWithOfficial(finmind,null).status,'OFFICIAL_PERIOD_NOT_YET_OBSERVED');
 assert.equal(compareWithOfficial(null,official()).status,'FINMIND_CAPTURE_MISSING');
 const missing=captureFinMindIncome(vals().filter(x=>!(x.date==='2026-06-30'&&x.type==='OperatingIncome')),'2330',clock);
 assert.equal(compareWithOfficial(missing,official()).status,'FINMIND_FIELDS_MISSING');
});

test('reject contradictory FinMind rows and mismatched ticker IDs',()=>{
 assert.throws(()=>captureFinMindIncome([...vals(),{...vals()[0],value:2}],'2330',clock),/Conflicting/);
 assert.throws(()=>captureFinMindIncome(vals('2454'),'2330',clock),/stock mismatch/);
});

test('market-wide reconciliation is audit-only and flags 79 uncaptured stocks',async()=>{
 const base=await mkdtemp(join(tmpdir(),'tw-reconcile-'));
 try{
  const research=join(base,'research'),filing=join(research,'filing-evidence','TW');
  for(const path of [
   join(research,'financial-versions'),join(research,'financial-source-rows','TW'),
   join(filing,'financial-statements'),join(filing,'otc-financial-statements')
  ])await mkdir(path,{recursive:true});
  const symbols=Array.from({length:80},(_,i)=>String(2330+i));
  await writeFile(join(research,'financial-versions','TW.json'),
   JSON.stringify(Object.fromEntries(symbols.map(sym=>[sym,{}]))));
  const entry=captureFinMindIncome(vals(),'2330',clock);
  await writeFile(join(research,'financial-source-rows','TW','2026-11-17--440111_1_TW.json'),
   JSON.stringify({cutoff:'2026-11-17',batch:'fake',stocks:{2330:entry}}));
  await writeFile(join(filing,'financial-statements','2026-11-17.json'),
   JSON.stringify({observations:[{fetchedAt:clock,data:[official()]}]}));
  await writeFile(join(filing,'otc-financial-statements','2026-11-17.json'),
   JSON.stringify({observations:[{fetchedAt:clock,data:[]}]}));
  const result=await reconcileOfficialAndFinMind(research,filing,'2026-11-17T04:00:00Z');
  assert.equal(result.states.VALUES_MATCHED_NOT_FILING_TIME_VERIFIED,1);
  assert.equal(result.states.FINMIND_CAPTURE_MISSING,79);
  const output=JSON.parse(await readFile(join(filing,'reconciliation','2026-11-17.json'),'utf8'));
  assert.equal(output.decision,'AUDIT_ONLY_NOT_AUTHORIZED_FOR_RETROACTIVE_TRADING');
  assert.equal(output.records.find(s=>s.symbol==='2330').eligibleForHistoricalPointInTimeBacktest,false);
  assert.equal(output.finmindAsOf,'2026-11-17');
 }finally{await rm(base,{recursive:true,force:true});}
});
