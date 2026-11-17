/**
 * Compare officially observed TWSE/TPEx YTD quarterly figures with FinMind's
 * single-quarter NT-dollar income rows. This is NOT original filing-time proof.
 *
 * FinMind TaiwanStockFinancialStatements: one quarter, NTD.
 * TWSE / TPEx t187ap06: YTD cumulative, NTD thousands.
 * Source: https://finmind.github.io/tutor/TaiwanMarket/Fundamental/
 */
import {createHash} from 'node:crypto';
import {readFile,readdir,mkdir,writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const digest=(value)=>createHash('sha256').update(value).digest('hex');
const QUARTER_RE=/^\d{4}-(03-31|06-30|09-30|12-31)$/;
const SOURCE='FinMind TaiwanStockFinancialStatements';
const OBSERVATION_STATES=[
  'VALUES_MATCHED_NOT_FILING_TIME_VERIFIED','VALUE_MISMATCH',
  'FINMIND_FIELDS_MISSING','PERIOD_NOT_AVAILABLE_IN_FINMIND',
  'OFFICIAL_PERIOD_NOT_YET_OBSERVED','FINMIND_CAPTURE_MISSING',
];
function numeric(value) {
  const v=Number(value);
  return value!==null&&value!==undefined&&value!==''&&Number.isFinite(v)?v:null;
}
const finite=(n)=>typeof n==='number'&&Number.isFinite(n);
const fullPeriod=(y,q)=>`${y}-${String(q*3).padStart(2,'0')}-${q===1||q===4?'31':'30'}`;

export function captureFinMindIncome(rows,symbol,firstObservedAt) {
  if(!Array.isArray(rows)||!/^\d{4}$/.test(symbol)||!Number.isFinite(Date.parse(firstObservedAt)))
    throw Error('Invalid FinMind income data, stock identifier or observation time');
  const values=new Map(),original=JSON.stringify(rows);
  for(const r of rows){
    if(r.stock_id&&String(r.stock_id)!==symbol)throw Error('FinMind income row stock mismatch: '+symbol);
    if(!QUARTER_RE.test(r.date??''))continue;
    if(!['Revenue','OperatingRevenue','OperatingIncome','OperatingProfit'].includes(r.type))continue;
    const v=numeric(r.value);
    if(!finite(v))continue;
    const row=values.get(r.date)||{date:r.date};
    if(Object.hasOwn(row,r.type)&&row[r.type]!==v)
      throw Error('Conflicting FinMind fiscal income values: '+symbol+' '+r.date+' '+r.type);
    row[r.type]=v;
    values.set(r.date,row);
  }
  const quarters=[...values.values()].sort((a,b)=>a.date.localeCompare(b.date)).slice(-12).map(row=>({
    date:row.date,
    revenue:row.Revenue??row.OperatingRevenue??null,
    operatingIncome:row.OperatingIncome??row.OperatingProfit??null,
    // Preserve which original field was actually compared.
    revenueType:finite(row.Revenue)?'Revenue':finite(row.OperatingRevenue)?'OperatingRevenue':null,
    operatingType:finite(row.OperatingIncome)?'OperatingIncome':finite(row.OperatingProfit)?'OperatingProfit':null
  }));
  return {schema:1,symbol,source:SOURCE,firstObservedAt,
    sourceResponseSha256:digest(original),quarters};
}

export function compareWithOfficial(finmind,official){
  if(!finmind)return {status:'FINMIND_CAPTURE_MISSING',detail:'缺 FinMind 同次下載的逐季原始數值'};
  if(!official)return {status:'OFFICIAL_PERIOD_NOT_YET_OBSERVED',detail:'證交所／櫃買中心尚無官方數值觀測'};
  const period=official.period;
  if(!QUARTER_RE.test(period)||!Number.isFinite(Date.parse(official.firstObservedAt))||
    !/^https:\/\//.test(official.sourceUrl))throw Error('Invalid official quarterly provenance');
  const y=Number(period.slice(0,4)),q=Number(period.slice(5,7))/3;
  const expected=Array.from({length:q},(_,i)=>fullPeriod(y,i+1));
  const quarters=expected.map(d=>finmind.quarters.find(row=>row.date===d));
  if(quarters.some(row=>!row))return {
    status:'PERIOD_NOT_AVAILABLE_IN_FINMIND',period,missingQuarters:expected.filter((d,i)=>!quarters[i]),
    detail:'FinMind 季資料未涵蓋官方年初至今累計的所有季度'};
  if(quarters.some(row=>!finite(row.revenue)||!finite(row.operatingIncome)))return {
    status:'FINMIND_FIELDS_MISSING',period,
    missingQuarters:expected.filter((d,i)=>!finite(quarters[i].revenue)||!finite(quarters[i].operatingIncome)),
    detail:'FinMind 收入或營業利益欄位不完整'};
  const revenueOfficial=numeric(official.revenue),operatingOfficial=numeric(official.operatingProfit);
  if(!finite(revenueOfficial)||!finite(operatingOfficial))return {
    status:'FINMIND_FIELDS_MISSING',period,detail:'官方收入或營業利益欄位無效'};
  // Official statement: thousand NTD YTD; FinMind: single-quarter NTD.
  const revenueFinmind=quarters.reduce((n,row)=>n+row.revenue,0)/1000;
  const opFinmind=quarters.reduce((n,row)=>n+row.operatingIncome,0)/1000;
  const tolerance=(a,b)=>Math.max(0.01,Math.abs(a)*1e-8,Math.abs(b)*1e-8);
  const revOk=Math.abs(revenueFinmind-revenueOfficial)<=tolerance(revenueFinmind,revenueOfficial);
  const opOk=Math.abs(opFinmind-operatingOfficial)<=tolerance(opFinmind,operatingOfficial);
  return {
    status:revOk&&opOk?'VALUES_MATCHED_NOT_FILING_TIME_VERIFIED':'VALUE_MISMATCH',
    period,officialSource:official.sourceUrl,
    officialFirstObservedAt:official.firstObservedAt,finmindFirstObservedAt:finmind.firstObservedAt,
    officialRowSha256:official.originalRowSha256,
    finmindResponseSha256:finmind.sourceResponseSha256,
    revenue:{officialYtdThousands:revenueOfficial,finmindSumQuarterThousands:revenueFinmind,matched:revOk},
    operatingIncome:{officialYtdThousands:operatingOfficial,finmindSumQuarterThousands:opFinmind,matched:opOk},
    comparedQuarters:expected,
    officialFilingTimeVerified:false,eligibleForHistoricalPointInTimeBacktest:false,
    detail:revOk&&opOk?'官方累計數值與 FinMind 單季加總一致；仍缺原始正式申報時間':'官方與 FinMind 數值不一致；禁止自動選用或補入回測'
  };
}

async function latestJson(folder){
  let names;
  try{names=(await readdir(folder)).filter(x=>/^\d{4}-\d{2}-\d{2}\.json$/.test(x)).sort();}
  catch(e){if(e.code==='ENOENT')return null;throw e;}
  if(!names.length)return null;
  return JSON.parse(await readFile(join(folder,names.at(-1)),'utf8'));
}

export async function reconcileOfficialAndFinMind(researchRoot,outputRoot,now=new Date().toISOString()){
  if(!Number.isFinite(Date.parse(now)))throw Error('Invalid reconciliation clock');
  const versions=JSON.parse(await readFile(join(researchRoot,'financial-versions','TW.json'),'utf8'));
  const symbols=Object.keys(versions).sort();
  if(symbols.length!==80)throw Error('Unexpected Taiwan stock universe');
  const input=await latestJson(join(researchRoot,'financial-source-rows','TW'));
  const listed=await latestJson(join(outputRoot,'financial-statements'));
  const otc=await latestJson(join(outputRoot,'otc-financial-statements'));
  const officialRows=[...(listed?.observations?.at(-1)?.data??[]),
    ...(otc?.observations?.at(-1)?.data??[])];
  const official=new Map();
  for(const row of officialRows){
    if(official.has(row.symbol))throw Error('Duplicate official TWSE/TPEx company '+row.symbol);
    official.set(row.symbol,row);
  }
  const finmind=new Map(Object.entries(input?.stocks??{}));
  const records=symbols.map(symbol=>{
    const comparison=compareWithOfficial(finmind.get(symbol),official.get(symbol));
    if(!OBSERVATION_STATES.includes(comparison.status))throw Error('Invalid reconciliation status');
    return {symbol,...comparison};
  });
  const counts=Object.fromEntries(OBSERVATION_STATES.map(s=>[s,records.filter(r=>r.status===s).length]));
  const out={
    schema:1,generatedAt:now,comparisonDate:now.slice(0,10),
    targetStocks:symbols.length,
    finmindAsOf:input?.cutoff??null,finmindBatch:input?.batch??null,
    officialTwseCaptureAt:listed?.observations?.at(-1)?.fetchedAt??null,
    officialTpexCaptureAt:otc?.observations?.at(-1)?.fetchedAt??null,
    states:counts,records,
    decision:'AUDIT_ONLY_NOT_AUTHORIZED_FOR_RETROACTIVE_TRADING',
    note:'Official first-seen does not prove original report filing time. Value equality does not certify as-of-close historical availability, statement corrections or cashflow completeness. Never alter financialVersions eligibleFrom or primary strategy checkpoints.'
  };
  const folder=join(outputRoot,'reconciliation');
  await mkdir(folder,{recursive:true});
  await writeFile(join(folder,now.slice(0,10)+'.json'),JSON.stringify(out)+'\n');
  return {date:out.comparisonDate,finmindAsOf:out.finmindAsOf,states:counts};
}
if(process.argv[1]&&fileURLToPath(import.meta.url)===process.argv[1]){
  const [, ,researchRoot,outputRoot]=process.argv;
  if(!researchRoot||!outputRoot)throw Error('Usage: node engine/tw-financial-reconciliation.mjs RESEARCH_DATA_ROOT FILING_EVIDENCE_ROOT');
  reconcileOfficialAndFinMind(researchRoot,outputRoot)
    .then(x=>process.stdout.write(JSON.stringify(x)+'\n'))
    .catch(e=>{console.error('Taiwan financial reconciliation failed: '+e.message);process.exitCode=1;});
}
