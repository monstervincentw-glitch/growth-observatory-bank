import test from 'node:test';
import assert from 'node:assert/strict';
import {reconstructDaily,HISTORICAL_DAYS} from '../engine/tw-rebuild-research-inputs.mjs';
const symbols=Array.from({length:80},(_,i)=>String(2000+i));
const allDates=Array.from({length:320},(_,i)=>
 new Date(Date.UTC(2026,10,16)-(319-i)*86400000).toISOString().slice(0,10));
const bars=allDates.map(date=>({date,open:100,close:100,adjOpen:100,adjClose:100,volume:4000000}));
const dates=['2023-12-31','2024-03-31','2024-06-30','2024-09-30','2024-12-31',
 '2025-03-31','2025-06-30','2025-09-30','2025-12-31','2026-03-31','2026-06-30','2026-09-30'];
const priceArchive={status:'POINT_IN_TIME_INPUT_VERIFIED',market:'TW',cutoff:'2026-11-16',
 stocks:Object.fromEntries(symbols.map(symbol=>[symbol,{
   name:'示例企業',industry:'電子',bars,features:{history_sessions:570}
 }]))};
const benchmarkArchive={status:'LIVE_RESEARCH_ONLY',benchmarks:{'0052':bars.map(x=>({...x,adjClose:1}))}};
const incomeSource={observedAt:'2026-11-17T04:00:00Z',stocks:Object.fromEntries(symbols.map(symbol=>[symbol,{
  quarters:dates.map(date=>({date,revenue:1000000,operatingIncome:100000,revenueType:'Revenue',operatingType:'OperatingIncome'}))}]))};
const cashflowSource={status:'COMPLETE',received:80,recordedAt:'2026-11-17T04:50:00Z',
 stocks:Object.fromEntries(symbols.map(symbol=>[symbol,{rows:dates.map(date=>({
 date,type:'CashFlowsFromOperatingActivities',value:100000,
 }))}]))};
const availability={status:'PDF_AND_QUOTE_AVAILABILITY_AUDIT_ONLY',
 dates:HISTORICAL_DAYS.slice(1).map(date=>({date,reportCoverage:80,priceCoverage:80}))};
const reportUpload={status:'COMPLETE',succeeded:80,generatedAt:'2026-11-17T04:00:00Z',
 selected:symbols.flatMap(symbol=>[
  {symbol,period:'2026-06-30',uploadedAt:'2026-08-14T13:59:44+08:00',
   document:`202602_${symbol}_AI1.pdf`,sourceUrl:'https://doc.twse.com.tw/server-java/t57sb01',
   approvedForHistoricalUse:false},
  {symbol,period:'2026-09-30',uploadedAt:'2026-11-13T13:47:09+08:00',
   document:`202603_${symbol}_AI1.pdf`,sourceUrl:'https://doc.twse.com.tw/server-java/t57sb01',
   approvedForHistoricalUse:false}
 ])};
test('rebuild exactly eight post-seal research days with next-session fiscal gates and keep them nontradable',()=>{
 const x=reconstructDaily({priceArchive,benchmarkArchive,incomeSource,cashflowSource,availability,reportUpload});
 assert.equal(x.status,'COMPLETE_RESEARCH_RECONSTRUCTION');
 assert.equal(x.days.length,9);
 assert.deepEqual(x.dates.map(z=>z.date),HISTORICAL_DAYS);
 assert.ok(x.dates.every(z=>z.stocks===80&&z.issues===0));
 assert.equal(x.days[7].stocks['2000'].financial.latest_period,'2026-06-30');
 assert.equal(x.days[8].stocks['2000'].financial.latest_period,'2026-09-30');
 assert.equal(x.days[8].stocks['2000'].bars.at(-1).date,'2026-11-16');
 assert.equal(x.days[8].stocks['2000'].financialVersion.verification,'ORIGINAL_PDF_TIME__LATER_OBSERVED_VENDOR_NUMERICS');
 assert.equal(x.days[8].tradeReplayVerified,false);
 assert.equal(x.strategyTradingReplayVerified,false);
 assert.equal(x.contemporaneousFinancialNumbersVerified,false);
});
test('missing original fiscal source rows fail closed instead of fabricating historical performance',()=>{
 const source=structuredClone(cashflowSource);
 source.stocks['2000'].rows=[];
 const x=reconstructDaily({priceArchive,benchmarkArchive,incomeSource,cashflowSource:source,availability,reportUpload});
 assert.equal(x.status,'PARTIAL_RESEARCH_RECONSTRUCTION');
 assert.ok(x.dates.every(z=>z.stocks===79&&z.issues===1));
});
