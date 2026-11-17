/**
 * Build an audit-only Taiwan daily research reconstruction from actual MOPS
 * report-upload clocks, historical Yahoo/FinMind price bars and later-observed
 * FinMind quarterly financial rows.
 *
 * DO NOT put these files in observed-primary-inputs or primary-inputs:
 * original historical financial-value revisions have not been compared with
 * each of the corresponding contemporaneous MOPS statement PDFs.
 */
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {twFinancial,priceFeatures} from './providers.ts';
import {SESSIONS} from './tw-pit-daily-availability.mjs';
const hash=raw=>createHash('sha256').update(raw).digest('hex');
const finite=x=>typeof x==='number'&&Number.isFinite(x);
export const HISTORICAL_DAYS=['2026-11-04',...SESSIONS];

export function reconstructDaily({priceArchive,benchmarkArchive,incomeSource,cashflowSource,
 availability,reportUpload,now='2026-11-17T05:00:00.000Z'}){
 if(priceArchive.status!=='POINT_IN_TIME_INPUT_VERIFIED'||priceArchive.market!=='TW'||
    priceArchive.cutoff!=='2026-11-16'||benchmarkArchive.status!=='LIVE_RESEARCH_ONLY'||
    cashflowSource.status!=='COMPLETE'||cashflowSource.received!==80||
    reportUpload.status!=='COMPLETE'||reportUpload.succeeded!==80||
    availability.status!=='PDF_AND_QUOTE_AVAILABILITY_AUDIT_ONLY'||
    availability.dates.length!==8)throw Error('TW historical sources not completely catalogued');
 const symbols=Object.keys(priceArchive.stocks).sort();
 if(symbols.length!==80||Object.keys(incomeSource.stocks).length!==80||
    Object.keys(cashflowSource.stocks).length!==80)throw Error('Missing raw Taiwan fiscal data');
 const bench=benchmarkArchive.benchmarks?.['0052'];
 if(!Array.isArray(bench)||bench.length<252||bench.at(-1)?.date!=='2026-11-16')
   throw Error('No verified historical benchmark reference for price indicators');
 const docsBySymbol=new Map();
 for(const doc of reportUpload.selected){
   if(!docsBySymbol.has(doc.symbol))docsBySymbol.set(doc.symbol,[]);
   docsBySymbol.get(doc.symbol).push(doc);
 }
 const results=[],daysMap=new Map(availability.dates.map(x=>[x.date,x]));
 for(const day of HISTORICAL_DAYS){
   const stocks={};const issues=[];
   const eligibleDay=daysMap.get(day);
   if(eligibleDay&&!(eligibleDay.priceCoverage===80&&eligibleDay.reportCoverage===80))
     throw Error('Official day availability matrix has a missing stock on '+day);
   for(const symbol of symbols){
     const origin=priceArchive.stocks[symbol];
     const bars=origin.bars.filter(x=>x.date<=day);
     const current=bars.at(-1),prev=bars.at(-2),anchor=origin.bars.find(x=>x.date==='2026-11-04');
     if(!current||!prev||!anchor||current.date!==day){
       issues.push({symbol,kind:'HISTORIC_QUOTE_MISSING'});continue;
     }
     const chosen=(docsBySymbol.get(symbol)??[])
       .filter(d=>d.period<=day&&d.uploadedAt.slice(0,10)<day&&d.approvedForHistoricalUse===false)
       .sort((a,b)=>b.period.localeCompare(a.period))[0];
     if(!chosen){issues.push({symbol,kind:'OFFICIAL_PDF_TIME_MISSING'});continue;}
     const quarters=incomeSource.stocks[symbol]?.quarters||[];
     const income=quarters.filter(x=>x.date<=chosen.period).flatMap(row=>[
       {date:row.date,type:row.revenueType??'Revenue',value:row.revenue},
       {date:row.date,type:row.operatingType??'OperatingIncome',value:row.operatingIncome}
     ]).filter(row=>finite(row.value));
     const cash=(cashflowSource.stocks[symbol]?.rows??[]).filter(x=>x.date<=chosen.period);
     const financial=twFinancial(income,cash,day);
     if(financial.financial_status!=='ok'||financial.latest_period!==chosen.period){
       issues.push({symbol,kind:'RETROSPECTIVE_NUMERICAL_FIELDS_INCOMPLETE',
         period:chosen.period,status:financial.financial_status});continue;
     }
     const benchmarkBars=bench.filter(x=>x.date<=day);
     const features=priceFeatures(bars,benchmarkBars);
     const futureBarCount=origin.bars.filter(x=>x.date>day).length;
     features.history_sessions=(origin.features.history_sessions||origin.bars.length)-futureBarCount;
     if(!finite(features.relative_63)||!finite(features.drawdown_126)||!finite(features.dollar_volume)){
       issues.push({symbol,kind:'HISTORICAL_TECHNICAL_INDICATOR_MISSING'});continue;
     }
     const eligibleTechnical=features.history_sessions>=252&&features.dollar_volume>=3e8;
     const div=features.drawdown_126>=-.5&&features.drawdown_126<=-.1&&features.relative_63<0;
     const compressed=[...new Map([prev,current,anchor].map(row=>[row.date,row])).values()]
       .sort((a,b)=>a.date.localeCompare(b.date));
     stocks[symbol]={
       financial:{...financial,filingEvidence:null},bars:compressed,features,
       industry:origin.industry,name:origin.name,marketEvent:null,
       evaluation:{tradable:eligibleTechnical,divergence:div,
         filingVerified:false},
       financialVersion:{
         period:chosen.period,source:'FinMind',
         firstVerifiedAt:cashflowSource.recordedAt,
         eligibleFrom:chosen.uploadedAt,
         officialPublishedAt:chosen.uploadedAt,officialSource:chosen.sourceUrl,
         verification:'ORIGINAL_PDF_TIME__LATER_OBSERVED_VENDOR_NUMERICS'
       },
       officialPDF:{file:chosen.document,uploadedAt:chosen.uploadedAt,period:chosen.period,
         sourceUrl:chosen.sourceUrl},
       originalVendorNumericalVintageVerified:false
     };
   }
   results.push({schema:1,market:'TW',cutoff:day,observedAt:now,
     status:'RETROSPECTIVE_RECONSTRUCTION_NOT_TRADE_VERIFIED',
     sourcePriceArchiveDate:priceArchive.cutoff,
     sourceCashflowObservedAt:cashflowSource.recordedAt,
     reportUploadAsOf:reportUpload.generatedAt,
     sourceIncomeObservedAt:incomeSource.observedAt,
     reconstructedCount:Object.keys(stocks).length,issues,stocks,
     financialNumericalVintageVerified:false,tradeReplayVerified:false,
     note:'Reconstructed from later-captured vendor numbers; original MOPS PDF upload times alone cannot attest contemporaneous source values.'
   });
 }
 return {schema:1,market:'TW',anchor:'2026-11-04',through:'2026-11-16',
   status:results.every(r=>r.reconstructedCount===80)?'COMPLETE_RESEARCH_RECONSTRUCTION':'PARTIAL_RESEARCH_RECONSTRUCTION',
   dates:results.map(r=>({date:r.cutoff,stocks:r.reconstructedCount,issues:r.issues.length})),
   sourceObservedAt:now,officialDocumentUploadTimesVerified:true,
   historicalPricesPresent:true,contemporaneousFinancialNumbersVerified:false,
   strategyTradingReplayVerified:false,days:results};
}
export async function buildFromResearch(root,output,at=new Date().toISOString()){
 const json=async p=>JSON.parse(await readFile(join(root,p),'utf8'));
 const [priceArchive,benchmarkArchive,incomeSource,cashflowSource,availability,reportUpload]=await Promise.all([
  json('observed-primary-inputs/TW/2026-11-16.json'),
  json('unverified-primary-inputs/TW/2026-11-16.json'),
  json('financial-source-rows/TW/2026-11-16--44012645235_1_TW.json'),
  json('filing-evidence/TW/replay-source/cashflow-reconstructed-2026-11-17.json'),
  json('filing-evidence/TW/daily-availability/2026-11-05_to_2026-11-16.json'),
  json('filing-evidence/TW/official-book-upload-review-2026-11-17.json')
 ]);
 const reconstruction=reconstructDaily({priceArchive,benchmarkArchive,incomeSource,
   cashflowSource,availability,reportUpload,now:at});
 await mkdir(output,{recursive:true});
 for(const row of reconstruction.days)await writeFile(join(output,row.cutoff+'.json'),
   JSON.stringify(row)+'\n');
 const report={...reconstruction,days:undefined};
 await writeFile(join(output,'status.json'),JSON.stringify(report,null,2)+'\n');
 console.log(JSON.stringify({status:report.status,dates:report.dates}));
 if(report.status!=='COMPLETE_RESEARCH_RECONSTRUCTION')process.exitCode=1;
 return reconstruction;
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
 const [, ,root,output]=process.argv;
 buildFromResearch(root,output).catch(e=>{console.error('TW research reconstruction failed: '+e.message);process.exitCode=1;});
}
