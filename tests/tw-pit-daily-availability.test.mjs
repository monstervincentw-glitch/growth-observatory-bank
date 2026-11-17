import test from 'node:test';
import assert from 'node:assert/strict';
import {buildDailyAvailability} from '../engine/tw-pit-daily-availability.mjs';
const symbols=Array.from({length:80},(_,i)=>String(2000+i));
const days=['2026-11-05','2026-11-06','2026-11-13','2026-11-16'];
const priceArchive={market:'TW',status:'POINT_IN_TIME_INPUT_VERIFIED',cutoff:'2026-11-16',
 stocks:Object.fromEntries(symbols.map(symbol=>[symbol,{bars:days.map(date=>({
 date,open:100,high:110,low:90,close:100,adjOpen:100,adjClose:100
 }))}]))};
const filing={schema:1,status:'COMPLETE',auditOnly:true,succeeded:80,requested:80,
 selected:symbols.map(symbol=>({symbol,period:'2026-06-30',
 uploadedAt:'2026-08-14T14:00:15+08:00',document:`202602_${symbol}_AI1.pdf`,
 reportKind:'CONSOLIDATED',sourceUrl:'https://doc.twse.com.tw/server-java/t57sb01?co_id='+symbol,
 approvedForHistoricalUse:false}))};
test('full 80 stocks use prior quarter until official new report becomes available next session',()=>{
 filing.selected.push({symbol:'2000',period:'2026-09-30',
 uploadedAt:'2026-11-13T13:47:09+08:00',document:'202603_2000_AI1.pdf',
 reportKind:'CONSOLIDATED',sourceUrl:'https://doc.twse.com.tw/server-java/t57sb01?co_id=2000',
 approvedForHistoricalUse:false});
 const out=buildDailyAvailability({filing,priceArchive,universe:symbols,sessions:days});
 assert.deepEqual(out.dates.map(x=>x.reportCoverage),[80,80,80,80]);
 assert.deepEqual(out.dates.map(x=>x.priceCoverage),[80,80,80,80]);
 assert.equal(out.dates[2].stocks['2000'].officialReport.period,'2026-06-30');
 assert.equal(out.dates[3].stocks['2000'].officialReport.period,'2026-09-30');
 assert.equal(out.strategyReplayVerified,false);
 assert.equal(out.originalFinancialNumbersReconciledPerHistoricalPeriod,false);
});
test('missing official upload or historical daily close cannot be labeled verified',()=>{
 const missing={...filing,selected:filing.selected.filter(x=>x.symbol!=='2001')};
 const missingPrice={...priceArchive,stocks:{...priceArchive.stocks,
  '2002':{bars:days.filter(x=>x!=='2026-11-05').map(date=>({date,open:1,close:1,adjOpen:1,adjClose:1}))}}};
 const out=buildDailyAvailability({filing:missing,priceArchive:missingPrice,universe:symbols,sessions:days});
 assert.equal(out.dates[0].reportCoverage,79);
 assert.equal(out.dates[0].priceCoverage,79);
 assert.equal(out.dates[0].allOfficialReportsAndPricesPresent,false);
 assert.deepEqual(out.missingOfficialReportSymbols,['2001']);
});
test('refuse partial official collection rather than misrepresent incomplete official evidence',()=>{
 assert.throws(()=>buildDailyAvailability({
 filing:{...filing,status:'PARTIAL'},priceArchive,universe:symbols,sessions:days}),/incomplete/);
});
