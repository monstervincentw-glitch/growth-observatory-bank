import test from 'node:test';
import assert from 'node:assert/strict';
import {mergeOfficialBookShards} from '../engine/tw-official-ebook-merge.mjs';
const symbols=Array.from({length:80},(_,i)=>String(2000+i));
const shards=Array.from({length:8},(_,shard)=>{
 const sel=symbols.filter((s,i)=>i%8===shard);
 return {schema:1,shards:8,shard,universeSize:80,years:[2026],stocks:10,
   selectedSymbols:sel,failures:[],requested:10,succeeded:10,
   receipts:sel.map(symbol=>({symbol,year:2026,sourceUrl:'https://doc.twse.com.tw',responseSha256:'a'.repeat(64)})),
   documents:sel.map(symbol=>({symbol,period:'2026-09-30',document:`202603_${symbol}_AI1.pdf`,
    uploadedAt:'2026-11-13T13:47:09+08:00',reportKind:'CONSOLIDATED',approvedForHistoricalUse:false}))};
});
test('merge official MOPS report batches requires entire 80-stock, 160-year-query universe',()=>{
 const x=mergeOfficialBookShards(shards,symbols);
 assert.equal(x.status,'COMPLETE');
 assert.equal(x.stocks,80);
 assert.equal(x.succeeded,80);
 assert.equal(x.q3_2026.available,80);
 assert.equal(x.auditOnly,true);
});
test('missing source shard or duplicated request never passes',()=>{
 assert.throws(()=>mergeOfficialBookShards(shards.slice(0,7),symbols));
 const dup=structuredClone(shards);
 dup[0].receipts[1]=dup[0].receipts[0];
 assert.throws(()=>mergeOfficialBookShards(dup,symbols),/Duplicate/);
 const partial=structuredClone(shards);
 partial[0].failures.push({symbol:'2000',year:2025,reason:'rate limited'});
 const result=mergeOfficialBookShards(partial,symbols);
 assert.equal(result.status,'PARTIAL');
});
