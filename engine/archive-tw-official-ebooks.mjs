/**
 * Point-in-time official financial-report PDF upload timestamps, directly from
 * MOPS/TWSE's electronic book server. Supports listed and OTC stock codes.
 * The PDF upload clock is official *document availability*, not proof that
 * a later FinMind financial-value revision was available then.
 */
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

export const OFFICIAL_HOST='https://doc.twse.com.tw/server-java/t57sb01';
const sha256=s=>createHash('sha256').update(s).digest('hex');
const quarterEnd=(y,q)=>`${y}-${['03-31','06-30','09-30','12-31'][q-1]}`;
const iso=(rocDate,hms)=>{
 const parts=/^(\d{2,3})\/(\d{2})\/(\d{2})$/.exec(rocDate);
 if(!parts||!/^\d{2}:\d{2}:\d{2}$/.test(hms))throw Error('Invalid official MOPS upload clock');
 const year=Number(parts[1])+1911,month=Number(parts[2]),day=Number(parts[3]);
 const t=new Date(Date.UTC(year,month-1,day));
 if(t.getUTCFullYear()!==year||t.getUTCMonth()!==month-1||t.getUTCDate()!==day||Number(hms.slice(0,2))>23||
    Number(hms.slice(3,5))>59||Number(hms.slice(6,8))>59)throw Error('Invalid MOPS upload date');
 return `${t.toISOString().slice(0,10)}T${hms}+08:00`;
};
function decodeCells(tr){
 const cells=[];
 for(const m of tr.matchAll(/<td\b[^>]*>([\s\S]*?)<\/td>/gi)){
  cells.push(m[1].replace(/<[^>]*>/g,' ').replace(/&nbsp;|&#160;/gi,' ').replace(/\s+/g,' ').trim());
 }
 return cells;
}
export function parseOfficialBook(html,symbol,year,observedAt,sourceUrl){
 if(!/^\d{4}$/.test(symbol)||!Number.isInteger(year)||year<2020||
    !Number.isFinite(Date.parse(observedAt))||!sourceUrl.startsWith(OFFICIAL_HOST+'?'))
   throw Error('MOPS electronic book provenance incomplete');
 const out=[];
 for(const row of html.matchAll(/<tr\b[^>]*>([\s\S]*?)<\/tr>/gi)){
  const t=row[1];
  const cells=decodeCells(t);
  if(cells.length<6||cells[0]!==symbol||!cells.some(c=>c==='財務報告書'))continue;
  const m=/readfile2\(\s*["']A["']\s*,\s*["'](\d{4})["']\s*,\s*["'](\d{4})(0[1-4])_(\d{4})_(AI[12AB])\.pdf["']\s*\)/i.exec(t);
  if(!m)continue;
  const [_,reportedSymbol,calendarYear,quarterCode,fileSymbol,variant]=m;
  if(reportedSymbol!==symbol||fileSymbol!==symbol)throw Error('MOPS PDF row stock mismatch');
  const q=Number(quarterCode),period=quarterEnd(Number(calendarYear),q);
  if(Number(calendarYear)!==year)continue;
  const stamp=cells.find(c=>/^\d{2,3}\/\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2}$/.test(c));
  if(!stamp)throw Error(`MOPS PDF upload timestamp missing ${symbol} ${period}`);
  const [rocDate,clock]=stamp.split(/\s+/);
  const kind=variant.toUpperCase()==='AI1'?'CONSOLIDATED':variant.toUpperCase()==='AI2'?'INDIVIDUAL':'FOREIGN_LANGUAGE';
  const pdf=`${calendarYear}${quarterCode}_${symbol}_${variant.toUpperCase()}.pdf`;
  out.push({symbol,period,document:pdf,reportKind:kind,uploadedAt:iso(rocDate,clock),
    firstObservedAt:observedAt,sourceUrl,evidenceType:'ORIGINAL_MOPS_FINANCIAL_REPORT_EBOOK',
    approvedForHistoricalUse:false,
    note:'Primary PDF upload timestamp. Original disclosed numbers and later vendor revisions require point-in-time numerical matching.'});
 }
 const key=new Set();
 for(const r of out){
   if(key.has(r.document))throw Error('Duplicate MOPS PDF listing: '+r.document);
   key.add(r.document);
 }
 return out.sort((a,b)=>a.period.localeCompare(b.period)||a.document.localeCompare(b.document));
}
export function chooseQuarterDocuments(rows){
 const map=new Map();
 for(const row of rows.filter(r=>r.reportKind!=='FOREIGN_LANGUAGE')){
   const key=row.symbol+'|'+row.period,old=map.get(key);
   if(!old||(row.reportKind==='CONSOLIDATED'&&old.reportKind!=='CONSOLIDATED')||
      (row.reportKind===old.reportKind&&row.uploadedAt<old.uploadedAt))map.set(key,row);
 }
 return [...map.values()].sort((a,b)=>a.symbol.localeCompare(b.symbol)||a.period.localeCompare(b.period));
}
export async function archiveOfficialBooks(registryPath,output,{years=[2026],fetcher=fetch,
 clock=()=>new Date(),sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms)),shard=0,shards=1}={}){
 const registry=JSON.parse(await readFile(registryPath,'utf8'));
 const symbols=Object.keys(registry).filter(x=>/^\d{4}$/.test(x)).sort();
 if(symbols.length!==80||!Number.isInteger(shard)||!Number.isInteger(shards)||shards<1||shard<0||shard>=shards)
   throw Error('Expected 80 tracked symbols and valid independent MOPS shard');
 const selectedSymbols=symbols.filter((s,i)=>i%shards===shard);
 const at=clock().toISOString(),cases=years.flatMap(year=>selectedSymbols.map(symbol=>({year,symbol})));
 const records=[],failures=[],receipts=[];
 // Slow bounded concurrency avoids creating a burst of hundreds of requests.
 let next=0;
 async function worker(){
   while(next<cases.length){
     const {year,symbol}=cases[next++];
     const params=new URLSearchParams({co_id:symbol,colorchg:'1',mtype:'A',step:'1',year:String(year-1911)});
     const url=OFFICIAL_HOST+'?'+params.toString();
     let done=false;
     for(let attempt=0;attempt<2&&!done;attempt++){
       try{
         const res=await fetcher(url,{headers:{'User-Agent':'Mozilla/5.0 (compatible; bank-research-audit/1.0)',
           Accept:'text/html,application/xhtml+xml','Accept-Language':'zh-TW,zh;q=0.9'},
           signal:AbortSignal.timeout(18000+attempt*10000)});
         if(!res.ok)throw Error('HTTP '+res.status);
         const raw=Buffer.from(await res.arrayBuffer());
         if(raw.byteLength>2_000_000)throw Error('Response too large');
         const html=new TextDecoder('big5').decode(raw);
         if(!html.includes('財務報告書'))throw Error('Official MOPS document listing missing');
         const parsed=parseOfficialBook(html,symbol,year,at,url);
         receipts.push({symbol,year,sourceUrl:url,firstObservedAt:at,
           responseSha256:sha256(raw),documentCount:parsed.length});
         records.push(...parsed);
         done=true;
       }catch(e){
         if(attempt===1)failures.push({symbol,year,reason:String(e.message||e).slice(0,140)});
         else await sleep(500);
       }
     }
     await sleep(600);
   }
 }
 await worker();
 const byYear={};
 for(const record of records){
   const y=record.period.slice(0,4);
   (byYear[y]??=[]).push(record);
 }
 const selected=chooseQuarterDocuments(records);
 const latest=selected.filter(r=>r.period==='2026-09-30');
 const result={schema:1,generatedAt:at,status:failures.length?'PARTIAL':'COMPLETE',
   shard,shards,years,stocks:selectedSymbols.length,selectedSymbols,universeSize:80,
   requested:cases.length,succeeded:receipts.length,
   failures,receipts:receipts.sort((a,b)=>a.symbol.localeCompare(b.symbol)||a.year-b.year),
   selected,documents:records.sort((a,b)=>a.symbol.localeCompare(b.symbol)||a.period.localeCompare(b.period)),
   q3_2026:{available:latest.length,publishedByClose:latest.filter(x=>x.uploadedAt<='2026-11-16T13:30:00+08:00').length,
     notYetObserved:selectedSymbols.filter(s=>!latest.some(x=>x.symbol===s))},
   auditOnly:true,
   caution:'A PDF upload timestamp cannot authenticate a reconstructed FinMind numeric version or transaction without original-version evidence.'
 };
 await mkdir(output,{recursive:true});
 await writeFile(join(output,`official-book-upload-review-2026-11-17.shard-${shard}.json`),
   JSON.stringify(result,null,2)+'\n');
 return {status:result.status,requested:result.requested,success:result.succeeded,
   documents:records.length,selected: selected.length,q3:result.q3_2026,failed:failures.slice(0,15)};
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
 const [, ,registry,destination,shardRaw='0',shardsRaw='1']=process.argv;
 archiveOfficialBooks(registry,destination,{shard:Number(shardRaw),shards:Number(shardsRaw)})
  .then(x=>{console.log(JSON.stringify(x));if(x.status!=='COMPLETE')process.exitCode=1;})
  .catch(e=>{console.error(e);process.exitCode=1;});
}
