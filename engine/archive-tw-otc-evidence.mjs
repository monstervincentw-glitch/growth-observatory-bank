/**
 * TPEx official OTC filing evidence. OTC stocks are excluded from the
 * TWSE's listed-company feeds. Keep these archives separate by exchange.
 *
 * Source print date / first observed time / material disclosure time never
 * automatically certifies the original quarterly filing time or numeric
 * equivalence to FinMind's point-in-time version.
 */
import {createHash} from 'node:crypto';
import {readFile,writeFile,mkdir} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {rocDay,parseDisclosureFeed,mergeEvidence} from './archive-tw-disclosures.mjs';
import {parseOfficialStatements} from './archive-tw-financial-statements.mjs';

export const OTC_FINANCIALS='https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap06_O_ci';
export const OTC_NOTICES='https://www.tpex.org.tw/openapi/v1/mopsfin_t187ap04_O';
const sha256=s=>createHash('sha256').update(s).digest('hex');

export function normalizeOTCDate(value){
  const s=String(value??'').replace(/[-/]/g,'').trim();
  if(/^\d{7}$/.test(s))return rocDay(s) && s;
  if(/^\d{8}$/.test(s)){
    const year=Number(s.slice(0,4))-1911;
    if(year<1||year>999)throw Error('Invalid TPEx report year');
    const roc=String(year).padStart(3,'0')+s.slice(4);
    rocDay(roc);
    return roc;
  }
  throw Error('Unknown TPEx official source date format: '+s.slice(0,12));
}

export function adaptOTCFinancialRow(raw) {
  return {
    '出表日期':normalizeOTCDate(raw.Date??raw['出表日期']),
    '年度':String(raw.Year??raw['年度']),
    '季別':String(raw.Season??raw['季別']),
    '公司代號':String(raw.SecuritiesCompanyCode??raw['公司代號']),
    '公司名稱':String(raw.CompanyName??raw['公司名稱']??''),
    '營業收入':raw['營業收入'],
    '營業利益（損失）':raw['營業利益（損失）']
  };
}

export function adaptOTCNoticeRow(raw) {
  return {
    '出表日期':normalizeOTCDate(raw.Date??raw['出表日期']),
    '發言日期':normalizeOTCDate(raw['發言日期']),
    '發言時間':String(raw['發言時間']??''),
    '公司代號':String(raw.SecuritiesCompanyCode??raw['公司代號']),
    '公司名稱':String(raw.CompanyName??raw['公司名稱']??''),
    '主旨':String(raw['主旨']??raw['主旨 ']??''),
    '符合條款':String(raw['符合條款']??''),
    '事實發生日':raw['事實發生日']?normalizeOTCDate(raw['事實發生日']):'',
    '說明':String(raw['說明']??'')
  };
}

async function jsonSource(fetcher,url,{pause=(ms)=>new Promise(resolve=>setTimeout(resolve,ms))}={}){
  // OTC endpoints sometimes stall. Retry only transient transport errors and
  // 502/503/504. Never hammer a 429 rate limit or retry invalid JSON/schema.
  for(let attempt=0;attempt<3;attempt++){
    try{
      const response=await fetcher(url,{headers:{Accept:'application/json'},
        signal:AbortSignal.timeout(20000+15000*attempt)});
      if(!response.ok)throw Error('Official TPEx endpoint HTTP '+response.status+': '+url);
      const body=await response.text();
      if(body.length>15_000_000)throw Error('TPEx official response is too large');
      const rows=JSON.parse(body);
      if(!Array.isArray(rows)||rows.length===0)throw Error('TPEx official response is empty');
      return {rows,hash:sha256(body)};
    }catch(error){
      const msg=String(error?.message??error);
      const transient=/timeout|aborted|ECONNRESET|fetch failed|network error|HTTP 50[234]\b/i.test(msg);
      if(!transient||attempt===2)throw error;
      console.warn('TPEx transient source error; retry '+(attempt+2)+' of 3 for '+url.split('/').at(-1));
      await pause(1500*(attempt+1));
    }
  }
  throw Error('Unreachable TPEx retry state');
}
async function existingJson(path, fallback){
  try{return JSON.parse(await readFile(path,'utf8'));}
  catch(e){if(e.code==='ENOENT')return fallback;throw e;}
}

export async function archiveOTC(registryPath,output,{fetcher=fetch,clock=()=>new Date()}={}){
  const registry=JSON.parse(await readFile(registryPath,'utf8'));
  const tracked=Object.keys(registry).filter(x=>/^\d{4}$/.test(x)).sort();
  if(tracked.length<60)throw Error('Insufficient Taiwan stock universe');
  const fetchedAt=clock().toISOString();
  const official=await jsonSource(fetcher,OTC_FINANCIALS);
  const adapted=official.rows.map(adaptOTCFinancialRow);
  const statement=parseOfficialStatements(adapted,tracked,fetchedAt);
  const withSource=statement.rows.map(row=>({
    ...row,sourceUrl:OTC_FINANCIALS,
    proofKind:'OFFICIAL_TPEX_FIRST_OBSERVED_FINANCIAL_VALUES',
    exchange:'TPEx',
  }));
  const releaseDate=fetchedAt.slice(0,10);
  const fileDir=join(output,'otc-financial-statements');
  const file=join(fileDir,releaseDate+'.json');
  const earlier=await existingJson(file,{schema:1,observations:[]});
  if(earlier.schema!==1||!Array.isArray(earlier.observations))
    throw Error('TPEx official financial archive incompatible');
  if(!earlier.observations.some(row=>row.fetchedAt===fetchedAt&&row.responseSha256===official.hash))
    earlier.observations.push({
      fetchedAt,sourceUrl:OTC_FINANCIALS,responseSha256:official.hash,
      responseRows:official.rows.length,sourcePrintDate:statement.reportDate,
      targetStocks:tracked.length,matchingStocks:statement.represented,
      unobservedInThisMarketSource:statement.missing,
      data:withSource,
      notice:'First-seen official OTC financial figures; not proof of original report filing clock or matching FinMind fields.'
    });
  earlier.observations.sort((a,b)=>a.fetchedAt.localeCompare(b.fetchedAt));

  const news=await jsonSource(fetcher,OTC_NOTICES);
  const notices=parseDisclosureFeed(news.rows.map(adaptOTCNoticeRow),tracked,fetchedAt,OTC_NOTICES);
  const groups=new Map();
  for(const row of notices){
    const day=row.publishedAt.slice(0,10);
    if(!groups.has(day))groups.set(day,[]);
    groups.get(day).push(row);
  }
  // Validate all inputs before writing any of the archive files.
  const staged=new Map();
  staged.set(file,earlier);
  const receipt={observedAt:fetchedAt,sourceUrl:OTC_NOTICES,
    responseSha256:news.hash,totalRecords:news.rows.length,matchingRecords:notices.length,
    targetStocks:tracked.length};
  const receiptFile=join(output,'otc-fetches',releaseDate+'.json');
  const receiptStore=await existingJson(receiptFile,{schema:1,date:releaseDate,sourceUrl:OTC_NOTICES,receipts:[]});
  if(receiptStore.schema!==1||!Array.isArray(receiptStore.receipts))
    throw Error('TPEx notice receipts incompatible');
  if(!receiptStore.receipts.some(row=>row.observedAt===fetchedAt&&row.responseSha256===news.hash))
    receiptStore.receipts.push(receipt);
  staged.set(receiptFile,receiptStore);
  for(const [day,rows] of groups){
    const path=join(output,'otc-announcements',day+'.json');
    const base=await existingJson(path,{schema:1,date:day,sourceUrl:OTC_NOTICES,receipts:[],records:[]});
    staged.set(path,mergeEvidence(base,rows,day,receipt));
  }
  for(const [path,record] of staged){
    await mkdir(path.slice(0,path.lastIndexOf('/')),{recursive:true});
    await writeFile(path,JSON.stringify(record)+'\n');
  }
  return {retrievedAt:fetchedAt,market:'TPEx',matchedFinancialStatements:statement.represented,
    noticeCount:notices.length,sourcePrintDate:statement.reportDate,
    unobservedInOTCFeed:statement.missing.length,
    sourceFinancial:OTC_FINANCIALS,sourceNotices:OTC_NOTICES};
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
  const [, ,registry,output]=process.argv;
  if(!registry||!output)throw Error('Usage: node engine/archive-tw-otc-evidence.mjs TW_VERSIONS_JSON EVIDENCE_FOLDER');
  archiveOTC(registry,output)
    .then(x=>process.stdout.write(JSON.stringify(x)+'\n'))
    .catch(error=>{console.error('Official OTC evidence archive failed: '+error.message);process.exitCode=1;});
}
