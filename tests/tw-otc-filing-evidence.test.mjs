import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {
 OTC_FINANCIALS,OTC_NOTICES,normalizeOTCDate,adaptOTCFinancialRow,
 adaptOTCNoticeRow,archiveOTC
} from '../engine/archive-tw-otc-evidence.mjs';

const universe=Array.from({length:64},(_,i)=>String(2300+i)).concat(Array.from({length:16},(_,i)=>String(3000+i)));
const financial={
  Date:'1151117',Year:'115',Season:'3',SecuritiesCompanyCode:'3000',
  CompanyName:'合成上櫃案例',營業收入:'150000.00','營業利益（損失）':'30000.00'
};
const disclosure={
  Date:'1151117','發言日期':'1151116','發言時間':'163158',
  SecuritiesCompanyCode:'3000',CompanyName:'合成上櫃案例',
  主旨:'公告115年第三季合併財務報告',
  符合條款:'31',事實發生日:'1151116',說明:'這是單元測試假資料'
};
test('TPEx fields, ROC or Gregorian dates and time boundaries are normalized',()=>{
 assert.equal(normalizeOTCDate('115/11/17'),'1151117');
 assert.equal(normalizeOTCDate('2026-11-17'),'1151117');
 assert.equal(adaptOTCFinancialRow(financial)['公司代號'],'3000');
 assert.equal(adaptOTCNoticeRow(disclosure)['發言日期'],'1151116');
 assert.throws(()=>normalizeOTCDate('2026-02-30'));
});
test('two official sources archive first-seen numbers and exact announcements without certifying filing time',async()=>{
 const root=await mkdtemp(join(tmpdir(),'otc-official-'));
 try{
  await writeFile(join(root,'registry.json'),JSON.stringify(Object.fromEntries(universe.map(s=>[s,{}]))));
  const mock=async url=>({ok:true,text:async()=>JSON.stringify(url===OTC_FINANCIALS?[financial]:[disclosure])});
  const time=()=>new Date('2026-11-17T03:40:00.000Z');
  const result=await archiveOTC(join(root,'registry.json'),join(root,'data'),{fetcher:mock,clock:time});
  assert.equal(result.matchedFinancialStatements,1);
  assert.equal(result.noticeCount,1);
  await archiveOTC(join(root,'registry.json'),join(root,'data'),{fetcher:mock,clock:time});
  const s=JSON.parse(await readFile(join(root,'data','otc-financial-statements','2026-11-17.json'),'utf8'));
  assert.equal(s.observations.length,1);
  assert.equal(s.observations[0].matchingStocks,1);
  assert.equal(s.observations[0].data[0].officialIndividualFilingTime,null);
  assert.equal(s.observations[0].data[0].eligibleForHistoricalPointInTimeBacktest,false);
  assert.equal(s.observations[0].data[0].sourceUrl,OTC_FINANCIALS);
  const n=JSON.parse(await readFile(join(root,'data','otc-announcements','2026-11-16.json'),'utf8'));
  assert.equal(n.records.length,1);
  assert.equal(n.records[0].publishedAt,'2026-11-16T16:31:58+08:00');
  assert.equal(n.records[0].officialFinancialReportVerified,false);
  assert.equal(n.records[0].sourceUrl,OTC_NOTICES);
 } finally{await rm(root,{recursive:true,force:true});}
});
test('official OTC financial outage blocks archival and is not silently reported as complete',async()=>{
 const root=await mkdtemp(join(tmpdir(),'otc-fail-'));
 try{
  await writeFile(join(root,'registry.json'),JSON.stringify(Object.fromEntries(universe.map(s=>[s,{}]))));
  await assert.rejects(archiveOTC(join(root,'registry.json'),join(root,'data'),{
    fetcher:async()=>({ok:false,status:429})
  }),/HTTP 429/);
 } finally{await rm(root,{recursive:true,force:true});}
});

test('official TPEx transient timeout retries once and still archives exact values',async()=>{
 const root=await mkdtemp(join(tmpdir(),'otc-retry-'));
 try{
  await writeFile(join(root,'registry.json'),JSON.stringify(Object.fromEntries(universe.map(s=>[s,{}]))));
  let statementRequests=0;
  const fetcher=async url=>{
    if(url===OTC_FINANCIALS&&++statementRequests===1)
      throw new Error('The operation was aborted due to timeout');
    return {ok:true,text:async()=>JSON.stringify(url===OTC_FINANCIALS?[financial]:[disclosure])};
  };
  const report=await archiveOTC(join(root,'registry.json'),join(root,'data'),{
    fetcher,clock:()=>new Date('2026-11-17T03:40:00Z')
  });
  assert.equal(statementRequests,2);
  assert.equal(report.matchedFinancialStatements,1);
  assert.equal(report.noticeCount,1);
  const file=JSON.parse(await readFile(join(root,'data','otc-financial-statements','2026-11-17.json'),'utf8'));
  assert.equal(file.observations[0].data[0].eligibleForHistoricalPointInTimeBacktest,false);
 }finally{await rm(root,{recursive:true,force:true});}
});
