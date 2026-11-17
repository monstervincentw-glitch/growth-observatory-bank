import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {chooseQuarterDocuments} from './archive-tw-official-ebooks.mjs';

export function mergeOfficialBookShards(shards,expectedSymbols) {
 if(shards.length!==8||expectedSymbols.length!==80||new Set(expectedSymbols).size!==80)
   throw Error('Expected eight 10-stock official filing archives');
 const receiptKeys=new Set(),symbolSet=new Set();
 const receipts=[],documents=[],failures=[];
 for(let shard=0;shard<shards.length;shard++){
   const item=shards[shard];
   if(item.schema!==1||item.shards!==8||item.shard!==shard||item.universeSize!==80||
      item.stocks!==10||item.selectedSymbols.length!==10||item.years.join(',')!=='2026')
      throw Error('Malformed or incorrect official PDF evidence shard '+shard);
   for(const symbol of item.selectedSymbols){
     if(symbolSet.has(symbol))throw Error('Duplicate company in MOPS evidence shards: '+symbol);
     symbolSet.add(symbol);
   }
   for(const receipt of item.receipts){
     const key=receipt.symbol+'|'+receipt.year;
     if(receiptKeys.has(key))throw Error('Duplicate official PDF query receipt '+key);
     receiptKeys.add(key);
     receipts.push(receipt);
   }
   failures.push(...item.failures);
   documents.push(...item.documents);
 }
 if(symbolSet.size!==80||expectedSymbols.some(s=>!symbolSet.has(s)))
   throw Error('Incomplete official issuer universe after evidence shard merge');
 const selected=chooseQuarterDocuments(documents);
 const q3=selected.filter(x=>x.period==='2026-09-30');
 return {
  schema:1,generatedAt:new Date().toISOString(),
  status:failures.length===0&&receiptKeys.size===80?'COMPLETE':'PARTIAL',
  years:[2026],stocks:80,requested:80,succeeded:receiptKeys.size,
  failures,receipts:receipts.sort((a,b)=>a.symbol.localeCompare(b.symbol)||a.year-b.year),
  selected,documents:documents.sort((a,b)=>a.symbol.localeCompare(b.symbol)||a.period.localeCompare(b.period)),
  q3_2026:{available:q3.length,publishedByClose:q3.filter(x=>x.uploadedAt<='2026-11-16T13:30:00+08:00').length,
    notYetObserved:expectedSymbols.filter(s=>!q3.some(r=>r.symbol===s))},
  auditOnly:true,sharding:{shards:8,issuerCount:80,yearQueries:80},
  caution:'Original report upload is not proof the same vendor financial numbers or trading decisions were available at a prior close.'
 };
}
export async function mergeFiles(registryPath,folder,output){
 const versions=JSON.parse(await readFile(registryPath,'utf8'));
 const shards=await Promise.all(Array.from({length:8},async(_,i)=>JSON.parse(
   await readFile(join(folder,`official-book-upload-review-2026-11-17.shard-${i}.json`),'utf8'))));
 const merged=mergeOfficialBookShards(shards,Object.keys(versions).sort());
 await mkdir(output,{recursive:true});
 await writeFile(join(output,'official-book-upload-review-2026-11-17.json'),
   JSON.stringify(merged,null,2)+'\n');
 console.log(JSON.stringify({status:merged.status,issuers:merged.stocks,
   successfulQueries:merged.succeeded,expected:merged.requested,
   documents:merged.documents.length,q3:merged.q3_2026,
   failed:merged.failures.slice(0,12)}));
 if(merged.status!=='COMPLETE')process.exitCode=1;
 return merged;
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
 const [, ,registry,shardFolder,output]=process.argv;
 mergeFiles(registry,shardFolder,output)
  .catch(e=>{console.error('Official MOPS shard merge failed: '+e.message);process.exitCode=1;});
}
