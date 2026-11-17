import test from 'node:test';
import assert from 'node:assert/strict';
import {parseOfficialBook,chooseQuarterDocuments,OFFICIAL_HOST} from '../engine/archive-tw-official-ebooks.mjs';
const source=OFFICIAL_HOST+'?co_id=2330&colorchg=1&mtype=A&step=1&year=115';
const row=(symbol,pdf,time,kind='IFRSs合併財報')=>
 `<tr><td>${symbol}</td><td>115 年 第三季</td><td>財務報告書</td><td>&nbsp;</td><td>&nbsp;</td><td>${kind}</td><td><a href='javascript:readfile2("A","${symbol}","${pdf}");'>${pdf}</a></td><td>6,393,437</td><td>${time}</td><td>無</td></tr>`;
test('parse original MOPS upload datetime and reject third party announcement as filing',()=>{
 const h='<table>'+row('2330','202603_2330_AI1.pdf','115/11/13 13:47:09')+
 row('2330','202603_2330_AIA.pdf','115/11/13 13:48:52','IFRSs英文版-合併財報')+'</table>';
 const records=parseOfficialBook(h,'2330',2026,'2026-11-17T04:30:00Z',source);
 assert.equal(records.length,2);
 const r=chooseQuarterDocuments(records);
 assert.equal(r.length,1);
 assert.equal(r[0].uploadedAt,'2026-11-13T13:47:09+08:00');
 assert.equal(r[0].reportKind,'CONSOLIDATED');
 assert.equal(r[0].period,'2026-09-30');
 assert.equal(r[0].approvedForHistoricalUse,false);
});
test('OTC individual reports are supported and exclude report types unrelated to financial PDFs',()=>{
 const url=OFFICIAL_HOST+'?co_id=3081&colorchg=1&mtype=A&step=1&year=115';
 const h='<table>'+row('3081','202603_3081_AI2.pdf','115/10/28 17:10:49','IFRSs個別財報')+'</table>';
 const r=chooseQuarterDocuments(parseOfficialBook(h,'3081',2026,'2026-11-17T04:30:00Z',url));
 assert.equal(r[0].reportKind,'INDIVIDUAL');
 assert.equal(r[0].uploadedAt,'2026-10-28T17:10:49+08:00');
});
test('reject malformed original document clocks, mismatched company and missing provenance',()=>{
 assert.throws(()=>parseOfficialBook(row('2330','202603_2330_AI1.pdf','115/11/32 13:47:09'),'2330',2026,'2026-11-17T04:30:00Z',source));
 assert.throws(()=>parseOfficialBook(row('2330','202603_2330_AI1.pdf','115/11/13 25:47:09'),'2330',2026,'2026-11-17T04:30:00Z',source));
 assert.throws(()=>parseOfficialBook(row('2330','202603_2330_AI1.pdf','115/11/13 13:47:09'),'2330',2026,'not-time',source));
 assert.throws(()=>parseOfficialBook(row('2330','202603_2454_AI1.pdf','115/11/13 13:47:09'),'2330',2026,'2026-11-17T04:30:00Z',source));
});
