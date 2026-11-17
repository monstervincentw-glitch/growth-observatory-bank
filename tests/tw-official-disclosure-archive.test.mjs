import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {archiveOfficialFeed, announcedAt,mergeEvidence,parseDisclosureFeed,rocDay,SOURCE} from '../engine/archive-tw-disclosures.mjs';

const input = (overrides={}) => ({
  '出表日期':'1151117',
  '發言日期':'1151113',
  '發言時間':'94531',
  '公司代號':'2330',
  '公司名稱':'台積電',
  '主旨 ':'公告本公司董事會通過115年第3季財務報告',
  '符合條款':'第31款',
  '事實發生日':'1151112',
  '說明':'僅為測試用虛構文字，並非台積電真實公告',
  ...overrides,
});

test('MOPS ROC date, five-digit times, and timezone are handled exactly',()=>{
  assert.equal(rocDay('1151113'),'2026-11-13');
  assert.equal(announcedAt('1151113','94531'),'2026-11-13T09:45:31+08:00');
  assert.equal(announcedAt('1151113','0'),'2026-11-13T00:00:00+08:00');
  for(const bad of ['1151132','1150230','1150001','1151232'])assert.throws(()=>rocDay(bad));
  for(const bad of ['246001','999999','abcd'])assert.throws(()=>announcedAt('1151113',bad));
});

test('official material announcement is NEVER auto-certified as quarterly financial filing',()=>{
  const [record] = parseDisclosureFeed([input()],['2330'],'2026-11-17T03:00:00.000Z');
  assert.equal(record.publishedAt,'2026-11-13T09:45:31+08:00');
  assert.equal(record.eventDate,'2026-11-12');
  assert.equal(record.firstObservedAt,'2026-11-17T03:00:00.000Z');
  assert.equal(record.officialFinancialReportVerified,false);
  assert.equal(record.evidenceKind,'TWSE_MOPS_MATERIAL_ANNOUNCEMENT');
  assert.equal(record.financialTopic,true);
  assert.equal(record.sourceUrl,SOURCE);
});

test('scheduled board meeting notice is never treated as a report completion',()=>{
  const [record] = parseDisclosureFeed([input({
    '主旨 ':'公告本公司預計召開董事會審議2026年第3季財務報告',
  })],['2330'],'2026-11-17T03:00:00.000Z');
  assert.equal(record.boardMeetingNoticeOnly,true);
  assert.equal(record.officialFinancialReportVerified,false);
});

test('exact announcement-time proof is append-only; duplicates are idempotent',()=>{
  const first = parseDisclosureFeed([input()],['2330'],'2026-11-17T03:00:00.000Z');
  const receipt = {observedAt:'2026-11-17T03:00:00.000Z',responseSha256:'x'};
  let output = mergeEvidence(null,first,'2026-11-13',receipt);
  const later = {...first[0],lastObservedAt:'2026-11-17T03:05:00.000Z'};
  output = mergeEvidence(output,[later],'2026-11-13',receipt);
  assert.equal(output.records.length,1);
  assert.equal(output.receipts.length,1);
  assert.equal(output.records[0].firstObservedAt,receipt.observedAt);
  assert.equal(output.records[0].lastObservedAt,later.lastObservedAt);
  assert.throws(()=>mergeEvidence(output,[{...later,originalRowSha256:'modified'}],'2026-11-13',receipt));
});

test('archives official feed by actual announcement day and receipt day, not event date',async()=>{
  const root=await mkdtemp(join(tmpdir(),'tw-evidence-'));
  try {
    const registry=Object.fromEntries(Array.from({length:80},(_,i)=>[String(2000+i),{}]));
    registry['2330']={};
    await writeFile(join(root,'versions.json'),JSON.stringify(registry));
    const body=JSON.stringify([input(),input({'公司代號':'9999','主旨 ':'not in universe'})]);
    const fakeFetch = async (url) => {
      assert.equal(url,SOURCE);
      return {ok:true,text:async()=>body};
    };
    const date=()=>new Date('2026-11-17T04:30:00.000Z');
    const result=await archiveOfficialFeed(join(root,'versions.json'),join(root,'archive'),{fetcher:fakeFetch,now:date});
    assert.equal(result.announcementCount,1);
    const day=JSON.parse(await readFile(join(root,'archive','announcements','2026-11-13.json'),'utf8'));
    assert.equal(day.records.length,1);
    assert.equal(day.records[0].officialFinancialReportVerified,false);
    const receipt=JSON.parse(await readFile(join(root,'archive','fetches','2026-11-17.json'),'utf8'));
    assert.equal(receipt.receipts[0].totalRecords,2);
    await archiveOfficialFeed(join(root,'versions.json'),join(root,'archive'),{fetcher:fakeFetch,now:date});
    const again=JSON.parse(await readFile(join(root,'archive','announcements','2026-11-13.json'),'utf8'));
    assert.equal(again.records.length,1);
  } finally {await rm(root,{recursive:true,force:true});}
});

test('API rejection or invalid payload prevents silent successful archive',async()=>{
  const root=await mkdtemp(join(tmpdir(),'tw-evidence-error-'));
  try {
    const reg=Object.fromEntries(Array.from({length:80},(_,i)=>[String(2000+i),{}]));
    await writeFile(join(root,'registry.json'),JSON.stringify(reg));
    await assert.rejects(archiveOfficialFeed(join(root,'registry.json'),join(root,'out'),{
      fetcher:async()=>({ok:false,status:429})
    }),/HTTP 429/);
  } finally {await rm(root,{recursive:true,force:true});}
});
