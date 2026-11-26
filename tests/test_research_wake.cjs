const vm=require('node:vm'),fs=require('node:fs'),assert=require('node:assert/strict');
let instant='2026-11-26T08:31:00Z',calls=0,ready=false;
const state=new Map([['PR_DONE_TW','2026-11-26']]); // Previous exhausted-day marker.
class Clock extends Date {constructor(...args){super(...(args.length?args:[instant]));}}
const context={Date:Clock,console:{log(){}},MP_BOOK:'research',
 PropertiesService:{getScriptProperties:()=>({getProperty:k=>state.get(k),setProperty:(k,v)=>state.set(k,v)})},
 Utilities:{formatDate:()=>new Date(new Date(instant).getTime()+8*3600000).toISOString().slice(11,16)},
 mpSession:m=>m==='TW'?'2026-11-26':null,
 SpreadsheetApp:{openById:()=>({getSheetByName:()=>null})},
 Sheets:{Spreadsheets:{Values:{get:()=>({values:[]})}}},
 verifyEnvelope_:()=>({markets:{TW:{macro:{marketAsOf:ready?'2026-11-26':'2026-11-25'}}}}),
 gnRead:()=>[{market:'TW',session_date:'2026-11-26',status:'FAILED',attempts:3}],
 UrlFetchApp:{fetch:()=>{calls++;return {getResponseCode:()=>200}}}};
vm.createContext(context);vm.runInContext(fs.readFileSync('apps-script/PipelineRunner.gs','utf8'),context);
context.syncPipelineLegacy=()=>{};
for(let i=0;i<13;i++)context.pulseResearchPipeline();
assert.equal(calls,12); // Bounded slot retry despite old false completion / failed attempt count.
instant='2026-11-26T09:31:00Z';context.pulseResearchPipeline();assert.equal(calls,13);
instant='2026-11-26T10:31:00Z';context.pulseResearchPipeline();assert.equal(calls,14);
instant='2026-11-26T13:28:00Z';context.pulseResearchPipeline();assert.equal(calls,15);
ready=true;context.pulseResearchPipeline();assert.equal(calls,15);
assert.equal(state.get('PR_VERIFIED_DONE_TW'),'2026-11-26');
context.pulseResearchPipeline();assert.equal(calls,15);
console.log('Per-slot recovery, exhausted markers, bounded retries and successful-close stop passed');
