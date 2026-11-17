/**
 * Archive official TWSE general-industry income statements as first-observed
 * snapshots. The "出表日期" of an exchange API response is NOT the individual
 * issuer's original filing time. These data never automatically approve a
 * historical point-in-time trading signal.
 */
import {createHash} from 'node:crypto';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {rocDay} from './archive-tw-disclosures.mjs';

export const OFFICIAL_STATEMENTS='https://openapi.twse.com.tw/v1/opendata/t187ap06_L_ci';
const hash=value=>createHash('sha256').update(value).digest('hex');

export function parseOfficialStatements(rows, tracked, fetchedAt){
  if(!Array.isArray(rows)||!Array.isArray(tracked)||tracked.length<60)
    throw Error('Invalid official TWSE income statement feed or tracked universe');
  if(!Number.isFinite(Date.parse(fetchedAt)))throw Error('Missing official retrieval timestamp');
  const symbols=new Set(tracked),matches=new Map();
  const dates=new Set();
  for(const raw of rows){
    if(!raw||typeof raw!=='object'||Array.isArray(raw))
      throw Error('Invalid TWSE income statement record');
    const symbol=String(raw['公司代號']??'').trim();
    if(!symbols.has(symbol))continue;
    const y=Number(raw['年度']),q=Number(raw['季別']);
    if(!Number.isInteger(y)||y<100||y>200||!Number.isInteger(q)||q<1||q>4)
      throw Error('Invalid TWSE reporting period for '+symbol);
    const period=`${1911+y}-${String(q*3).padStart(2,'0')}-${q===1||q===4?'31':'30'}`;
    const asOf=rocDay(raw['出表日期']);
    dates.add(asOf);
    if(matches.has(symbol))throw Error('Unexpected duplicate TWSE income statement for '+symbol);
    const revenue=String(raw['營業收入']??'').trim();
    const operatingProfit=String(raw['營業利益（損失）']??'').trim();
    if(!/^-?\d+(?:\.\d+)?$/.test(revenue)||!/^-?\d+(?:\.\d+)?$/.test(operatingProfit))
      throw Error('TWSE income statement missing key figures for '+symbol);
    matches.set(symbol,{
      symbol,company:String(raw['公司名稱']??''),period,
      sourcePrintDate:asOf,firstObservedAt:fetchedAt,
      revenue,operatingProfit,unit:'TWSE source amounts (NTD thousands per official dataset conventions)',
      originalRowSha256:hash(JSON.stringify(raw)),
      sourceUrl:OFFICIAL_STATEMENTS,
      proofKind:'OFFICIAL_TWSE_FIRST_OBSERVED_FINANCIAL_VALUES',
      officialIndividualFilingTime:null,
      matchesFinMindVersion:false,
      eligibleForHistoricalPointInTimeBacktest:false,
    });
  }
  if(matches.size===0||dates.size>1)
    throw Error('Official TWSE statement feed is empty or spans inconsistent print dates');
  return {rows:[...matches.values()].sort((a,b)=>a.symbol.localeCompare(b.symbol)),
    reportDate:[...dates][0],missing:[...symbols].filter(x=>!matches.has(x)).sort(),
    represented:matches.size};
}

export async function archiveOfficialStatements(registryPath,root,{fetcher=fetch,clock=()=>new Date()}={}){
  const registry=JSON.parse(await readFile(registryPath,'utf8'));
  const tracked=Object.keys(registry).filter(x=>/^\d{4}$/.test(x)).sort();
  const fetchedAt=clock().toISOString();
  const res=await fetcher(OFFICIAL_STATEMENTS,{headers:{Accept:'application/json'},
    signal:AbortSignal.timeout(20000)});
  if(!res.ok)throw Error('TWSE official financial statement HTTP '+res.status);
  const body=await res.text();
  if(body.length>12_000_000)throw Error('Official financial statements unexpectedly large');
  const response=JSON.parse(body),decoded=parseOfficialStatements(response,tracked,fetchedAt);
  const record={
    schema:1,sourceUrl:OFFICIAL_STATEMENTS,fetchDay:fetchedAt.slice(0,10),
    reportDate:decoded.reportDate,targetStocks:tracked.length,
    responseSha256:hash(body),responseRows:response.length,
    matchingStocks:decoded.represented,missingStocks:decoded.missing,
    data:decoded.rows,
    notice:"Source print date and first observed time do not prove issuer original filing time. Official report upload audit and FinMind numerical reconciliation remain required.",
  };
  const directory=join(root,'financial-statements');
  await mkdir(directory,{recursive:true});
  const path=join(directory,record.fetchDay+'.json');
  let older={schema:1,observations:[]};
  try{older=JSON.parse(await readFile(path,'utf8'));}
  catch(e){if(e.code!=='ENOENT')throw e;}
  if(older.schema!==1||!Array.isArray(older.observations))
    throw Error('Existing official income statement archive incompatible');
  if(!older.observations.some(row=>row.fetchedAt===fetchedAt&&row.responseSha256===record.responseSha256))
    older.observations.push({fetchedAt, ...record});
  older.observations.sort((a,b)=>a.fetchedAt.localeCompare(b.fetchedAt));
  await writeFile(path,JSON.stringify(older)+'\n');
  return {reportDate:decoded.reportDate,firstObservedAt:fetchedAt,
    targetStocks:tracked.length,matchingStocks:decoded.represented,
    missingStocks:decoded.missing.length,source:OFFICIAL_STATEMENTS};
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
  const [, ,registry,output]=process.argv;
  if(!registry||!output)throw Error('Usage: node engine/archive-tw-financial-statements.mjs VERSIONS_JSON EVIDENCE_FOLDER');
  archiveOfficialStatements(registry,output)
    .then(result=>process.stdout.write(JSON.stringify(result)+'\n'))
    .catch(error=>{console.error('Official TWSE financial archive failed: '+error.message);process.exitCode=1;});
}
