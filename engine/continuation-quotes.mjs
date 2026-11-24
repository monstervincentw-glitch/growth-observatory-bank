// Only prices and missing original weekly macro vintages; never refetch financials.
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {yahoo,readMacroVintage} from './providers.ts';
import {financialVersions} from './financial-versions.mjs';
import {archiveEtf} from './replay-export.mjs';
const [input,root,runtimePath]=process.argv.slice(2);
const snapshot=JSON.parse(await readFile(input,'utf8'));
const runtime=runtimePath?JSON.parse(await readFile(runtimePath,'utf8')):{};
const all={};
const benchmarks={};
for(const market of ['US','TW']){
 const block=snapshot.markets[market], day=block.macro.marketAsOf;
 benchmarks[market]={};
 for(const name of market==='US'?['QQQ','SOXX','IGV']:['0052'])benchmarks[market][name]=await yahoo(market==='TW'?'0052.TW':name,market,day);
 all[market]={};
 const registry=JSON.parse(await readFile(root+'/financial-versions/'+market+'.json','utf8'));
 for(const stock of block.stocks){
  if(stock.marketEvent)continue;
  registry[stock.symbol]=financialVersions(stock,stock,registry[stock.symbol],day+(market==='TW'?'T13:30:00+08:00':'T16:00:00-05:00'),new Date().toISOString());
 }
 await writeFile(root+'/financial-versions/'+market+'.json',JSON.stringify(registry));
 const active=block.stocks.filter(s=>!s.marketEvent);
 for(let n=0;n<active.length;n+=3){
  await Promise.all(active.slice(n,n+3).map(async s=>{
   const bars=runtime[market]?.bars?.[s.symbol]??await yahoo(s.ticker,market,day);
   if(bars.at(-1)?.date!==day||Math.abs(bars.at(-1).close-s.close)/s.close>.001)throw Error('Published price mismatch '+market+':'+s.symbol);
   all[market][s.symbol]=bars;
  }));
 }
}
await mkdir(root,{recursive:true});
await writeFile(root+'/continuation-quotes.json',JSON.stringify({bars:all,benchmarks}));
const {readdir}=await import('node:fs/promises');
let known=[];try{known=(await readdir(root+'/weekly-vintages')).map(s=>s.replace('.json',''));}catch{}
await archiveEtf(root,{cutoff:snapshot.markets.US.macro.marketAsOf,benchmarks:benchmarks.US,createdAt:snapshot.generatedAt},'sheets-continuation',new Set(known));
