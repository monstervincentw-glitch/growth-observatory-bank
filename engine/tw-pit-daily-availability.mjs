/**
 * Strict daily close availability matrix for the 2026-11-05 to 11-16 TW
 * research gap. It proves original PDF availability and daily quote coverage,
 * NEVER reconstructs financial factors/trades from a later vendor snapshot.
 */
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
export const SESSIONS=['2026-11-05','2026-11-06','2026-11-09','2026-11-10',
  '2026-11-11','2026-11-12','2026-11-13','2026-11-16'];
const hasValue=x=>typeof x==='number'&&Number.isFinite(x)&&x>0;
export function buildDailyAvailability({filing,priceArchive,universe,sessions=SESSIONS}){
 if(!Array.isArray(universe)||universe.length!==80||new Set(universe).size!==80)
   throw Error('Unexpected tracked stock universe');
 if(filing.schema!==1||filing.status!=='COMPLETE'||filing.auditOnly!==true||
    filing.succeeded!==filing.requested)throw Error('Official PDF report audit incomplete');
 if(priceArchive.market!=='TW'||priceArchive.cutoff!=='2026-11-16'||
    !['POINT_IN_TIME_INPUT_VERIFIED','RESEARCH_VERIFIED'].includes(priceArchive.status))
   throw Error('Price reference must be verified TW 2026-11-16 research archive');
 const official=filing.selected??[];
 const values=priceArchive.stocks;
 if(!values||Object.keys(values).length!==80)throw Error('Inadequate instrument price universe');
 const results=[],globalMissing=new Set();
 for(const day of sessions){
   const items={};let officialCount=0,priceCount=0;
   for(const symbol of universe){
     const quotes=values[symbol]?.bars??[];
     const q=quotes.find(p=>p.date===day);
     const priceValid=!!q&&hasValue(q.adjClose)&&hasValue(q.adjOpen)&&hasValue(q.open)&&hasValue(q.close);
     const docs=official.filter(d=>d.symbol===symbol&&d.period<=day&&
       d.uploadedAt.slice(0,10)<day&&d.approvedForHistoricalUse===false)
      .sort((a,b)=>b.period.localeCompare(a.period)||a.uploadedAt.localeCompare(b.uploadedAt));
     const doc=docs[0]??null;
     if(priceValid)priceCount++;
     if(doc)officialCount++;
     if(!doc)globalMissing.add(symbol);
     items[symbol]={
       officialReport:doc?{period:doc.period,pdf:doc.document,uploadedAt:doc.uploadedAt,
         type:doc.reportKind,sourceUrl:doc.sourceUrl}:null,
       officialReportEligibleForReview:!!doc,
       quotePresent:priceValid,
       quoteDate:priceValid?day:null,
       // Availability is NOT an attestation of the actual FinMind numerical
       // version, exact trade selection or post-signal order execution.
       decisionVerified:false,
     };
   }
   results.push({date:day,stocks:items,reportCoverage:officialCount,priceCoverage:priceCount,
     allOfficialReportsAndPricesPresent:officialCount===80&&priceCount===80,
     replayTradeDecisionVerified:false});
 }
 return {schema:1,status:'PDF_AND_QUOTE_AVAILABILITY_AUDIT_ONLY',
   anchor:'2026-11-04',through:sessions.at(-1),
   reportSource:'Original MOPS electronic report PDF timestamps',
   priceSource:'2026-11-16 verified TW history bars; not archived same-day decisions',
   strictNextTradingSession:true,scope:'TW',universe:universe.length,
   dates:results,missingOfficialReportSymbols:[...globalMissing].sort(),
   originalFinancialNumbersReconciledPerHistoricalPeriod:false,
   strategyReplayVerified:false,
   caveat:'PDF availability and historical prices alone do not verify earlier market-time financial factor versions, ranking, macro vintage or next-open fills.'};
}
export async function createDailyAvailability(researchDir,sourceFile,destinationDir){
 const versions=JSON.parse(await readFile(join(researchDir,'financial-versions','TW.json'),'utf8'));
 const universe=Object.keys(versions).sort();
 const filing=JSON.parse(await readFile(sourceFile,'utf8'));
 const snapshot=JSON.parse(await readFile(join(researchDir,'observed-primary-inputs','TW','2026-11-16.json'),'utf8'));
 const result=buildDailyAvailability({filing,priceArchive:snapshot,universe});
 await mkdir(destinationDir,{recursive:true});
 await writeFile(join(destinationDir,'2026-11-05_to_2026-11-16.json'),JSON.stringify(result)+'\n');
 console.log(JSON.stringify({status:result.status,dates:result.dates.map(x=>({
   date:x.date,official:x.reportCoverage,prices:x.priceCoverage})),missingSymbols:result.missingOfficialReportSymbols,
   replayVerified:result.strategyReplayVerified}));
 return result;
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
 const [, ,researchDir,sourceFile,outDir]=process.argv;
 createDailyAvailability(researchDir,sourceFile,outDir)
   .catch(e=>{console.error('TW historical PIT evidence matrix failed: '+e.message);process.exitCode=1;});
}
