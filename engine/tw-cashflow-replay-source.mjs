/**
 * Collect historical FinMind Taiwan quarterly operating cashflow rows once,
 * paired with the already archived per-quarter income source.
 * Acquired 2026-11-17; this is reconstruction evidence, not PIT prior capture.
 */
import {finmind} from './providers.ts';
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
const sha=s=>createHash('sha256').update(s).digest('hex');
export async function archiveTwCashflow(registryPath,out,{fetcher=finmind,clock=()=>new Date(),
  sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms))}={}){
 const reg=JSON.parse(await readFile(registryPath,'utf8'));
 const symbols=Object.keys(reg).sort();
 if(symbols.length!==80)throw Error('Expected 80 TW tickers');
 const at=clock().toISOString(),stocks={},errors={};
 for(const symbol of symbols){
  try{
    const data=await fetcher('TaiwanStockCashFlowsStatement',symbol,'2023-01-01','2026-11-16');
    if(!Array.isArray(data))throw Error('Cashflow rows missing');
    const rows=data.filter(r=>r.stock_id===symbol&&
      ['CashFlowsFromOperatingActivities','NetCashInflowFromOperatingActivities'].includes(r.type)&&
      /^\d{4}-\d{2}-\d{2}$/.test(r.date)&&r.date<='2026-11-16'&&
      typeof r.value==='number'&&Number.isFinite(r.value))
      .map(r=>({date:r.date,type:r.type,value:r.value,stock_id:symbol}))
      .sort((a,b)=>a.date.localeCompare(b.date)||a.type.localeCompare(b.type));
    if(!rows.some(r=>r.date==='2026-06-30'))throw Error('2026 Q2 cashflow not present');
    if(new Set(rows.map(x=>x.date+'|'+x.type)).size!==rows.length)
      throw Error('Duplicate fiscal-period cashflow types');
    stocks[symbol]={firstObservedAt:at,dataset:'TaiwanStockCashFlowsStatement',
      sourceUrl:'https://api.finmindtrade.com/api/v4/data',
      sourceRowsSha256:sha(JSON.stringify(data)),rows};
  }catch(e){errors[symbol]=String(e.message||e).slice(0,170);}
  await sleep(250);
 }
 const doc={schema:1,recordedAt:at,source:'FinMind TaiwanStockCashFlowsStatement',
  status:Object.keys(errors).length?'PARTIAL':'COMPLETE',yearRange:['2023','2026'],
  asOfVendorRequest:'2026-11-16',received:Object.keys(stocks).length,
  expected:80,stocks,errors,originalHistoricalCapture:false,
  auditOnly:true,
  caution:'Retrieved later than the historical 11/5–11/13 sessions; must reconcile numeric fiscal vintage before certifying old trades.'};
 await mkdir(out,{recursive:true});
 await writeFile(join(out,'cashflow-reconstructed-2026-11-17.json'),
   JSON.stringify(doc)+'\n');
 console.log(JSON.stringify({status:doc.status,received:doc.received,expected:80,
   q3Available:Object.values(stocks).filter(v=>v.rows.some(r=>r.date==='2026-09-30')).length,
   errors:doc.errors}));
 if(doc.status!=='COMPLETE')process.exitCode=1;
 return doc;
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
 const [, ,registry,out]=process.argv;
 archiveTwCashflow(registry,out).catch(e=>{console.error(e);process.exitCode=1;});
}
