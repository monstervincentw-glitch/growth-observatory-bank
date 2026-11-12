import type {Macro,MacroDecision,RiskFlag} from './types.ts';


export const MACRO_RULE = 'grouped-2of3-20261111-tristate';
export const SERIES = ['CPIAUCSL','FEDFUNDS','DFII10','NFCI'] as const;
export type Series = typeof SERIES[number];
export type MacroSeriesInput = {vintageDate:string;series:Record<string,string>;cpiVintageDate?:string};
const LIMIT:Record<Series,number> = {CPIAUCSL:77,FEDFUNDS:77,DFII10:9,NFCI:16};
type Row = {date:string;value:number};
type Snapshot = {vintage:string; rows:Row[]};
const confirmedCpiReleases:Record<string,{at:string;source:string}> = {
 '2026-10-01':{at:'2026-11-10T08:30:00-05:00',source:'https://www.bls.gov/news.release/archives/cpi_11102026.htm'}
};
export function latestCpiVintage(asof:string,cutoff:string,allowIntraday:boolean){
 const releases=Object.values(confirmedCpiReleases).map(r=>r.at.slice(0,10));
 return allowIntraday&&releases.includes(cutoff)?cutoff:asof;
}

export function dateOffset(day:string,offset:number){
 return new Date(Date.parse(day+'T12:00:00Z')+offset*86400000).toISOString().slice(0,10);
}

/** ALFRED dates are US calendar dates, without an intraday publication guarantee.
 * At either market's close only the preceding New York calendar day is complete.
 */
export const macroCutoff = (marketCloseDate:string) => dateOffset(marketCloseDate,-1);
const validDate=(date:string)=>/^\d{4}-\d{2}-\d{2}$/.test(date)&&Number.isFinite(Date.parse(date+'T12:00:00Z'))&&dateOffset(date,0)===date;

export function parseFredCsv(series:Series,asof:string,csv:string):Snapshot{
 if(typeof csv!=='string'||csv.length>600000)throw new Error(series+' 原始資料格式或大小無效');
 const [header,...lines]=csv.trim().replace(/^\ufeff/,'').split(/\r?\n/);
 const compact=header.match(new RegExp('^observation_date,'+series+'_(\\d{8})$'))?.[1];
 const vintage=compact?compact.replace(/^(\d{4})(\d{2})(\d{2})$/,'$1-$2-$3'):'';
 if(!validDate(vintage)||vintage>asof)throw new Error(series+' 版本日晚於可用截止日或格式無效');
 const byDate=new Map<string,number>();
 for(const line of lines){
  const [date,raw]=line.split(',');
  if(!validDate(date)||date>vintage||!raw?.trim()||raw==='.')continue;
  const value=Number(raw);
  if(!Number.isFinite(value))continue;
  if(byDate.has(date)&&byDate.get(date)!==value)throw new Error(series+' 有衝突觀測值');
  byDate.set(date,value);
 }
 const rows=[...byDate].sort(([a],[b])=>a.localeCompare(b)).map(([date,value])=>({date,value}));
 const last=rows.at(-1);
 if(!last||last.date<dateOffset(asof,-LIMIT[series]))throw new Error(series+' 缺值或已逾觀測時效');
 return {vintage,rows};
}

function evidence(snapshots:Partial<Record<Series,Snapshot>>,asof:string,timestamp:string){
 const present=SERIES.filter(s=>snapshots[s]);
 return {
  series_vintage_dates:Object.fromEntries(present.map(s=>[s,snapshots[s]!.vintage])),
  seriesMeta:Object.fromEntries(present.map(s=>{
   const snapshot=snapshots[s]!,observationDate=snapshot.rows.at(-1)!.date;
   const release=s==='CPIAUCSL'?confirmedCpiReleases[observationDate]:undefined;
   return [s,{observationDate,vintageDate:snapshot.vintage,publicationAt:release?.at??null,publicationSource:release?.source,
    availableBy:release?.at??dateOffset(asof,1)+' 00:00 America/New_York（保守日期界線）',acquiredAt:timestamp}];
  }))
 };
}

export function decideFunding(categories:NonNullable<Macro['categoryStates']>){
 const flags=Object.values(categories);
 const min=flags.filter(x=>x===1).length;
 const max=min+flags.filter(x=>x===null).length;
 const decision:MacroDecision=min>=2?'red':max<2?'non_red':'unknown';
 return {decision,scoreRange:[min,max] as [number,number]};
}

function calculateMacro(cutoff:string,asof:string,raw:Record<string,string>,clock:Date,cpiVintage:string,transportError?:unknown):Macro{
 if(asof!==macroCutoff(cutoff))throw new Error('總經快照截止日無效');
 const snapshots:Partial<Record<Series,Snapshot>>={},issues:string[]=[];
 for(const s of SERIES){
  try{snapshots[s]=parseFredCsv(s,s==='CPIAUCSL'?cpiVintage:asof,raw[s]);}
  catch(error){issues.push(error instanceof Error?error.message:String(error));}
 }
 const last=(s:Series)=>snapshots[s]?.rows.at(-1);
 function delta(s:Series){
  const current=last(s);
  if(!current)return null;
  const threshold=dateOffset(current.date,-90);
  const base=snapshots[s]?.rows.findLast(row=>row.date<=threshold);
  return base?current.value-base.value:null;
 }
 const realDelta=delta('DFII10'),policyDelta=delta('FEDFUNDS'),fcDelta=delta('NFCI');
 const real:RiskFlag=realDelta===null?null:realDelta>.50?1:0;
 const policy:RiskFlag=policyDelta===null?null:policyDelta>.25?1:0;
 const rates:RiskFlag=real===1||policy===1?1:real===0&&policy===0?0:null;
 const fc=last('NFCI')?.value;
 const conditions:RiskFlag=(fc!==undefined&&fc>0)||(fcDelta!==null&&fcDelta>.25)?1:fc!==undefined&&fcDelta!==null?0:null;
 const cpi=last('CPIAUCSL');
 let yoy:number|null=null,annual:number|null=null,inflation:RiskFlag=null;
 if(cpi){
  if(cpiVintage!==asof&&confirmedCpiReleases[cpi.date]?.at.slice(0,10)!==cpiVintage){
   issues.push('已公布的當日 CPI 尚未出現在 ALFRED 快照');
  }else{
   const [year,month]=cpi.date.split('-').map(Number);
   const comparison=(months:number)=>snapshots.CPIAUCSL?.rows.find(row=>row.date.startsWith(new Date(Date.UTC(year,month-1-months,1)).toISOString().slice(0,7)))?.value;
   const yearAgo=comparison(12),threeAgo=comparison(3);
   if(!(yearAgo&&yearAgo>0&&cpi.value>0))issues.push('CPI 缺少年增率基準（'+(year-1)+'-'+String(month).padStart(2,'0')+'）');
   else {
    yoy=cpi.value/yearAgo-1;
    annual=threeAgo&&threeAgo>0?Math.pow(cpi.value/threeAgo,4)-1:null;
    if(yoy<=.04)inflation=0;
    else if(annual!==null)inflation=annual>yoy?1:0;
    else issues.push('CPI 年增逾4%，缺少3個月加速判斷基準');
   }
  }
 }
 for(const [s,d] of [['DFII10',realDelta],['FEDFUNDS',policyDelta],['NFCI',fcDelta]] as const)
  if(d===null&&snapshots[s])issues.push(s+' 缺少90日基準');
 const categoryStates={rates,conditions,inflation};
 const {decision,scoreRange}=decideFunding(categoryStates);
 const full=scoreRange[0]===scoreRange[1];
 const timestamp=clock.toISOString();
 const problems=[...new Set([...issues,...(transportError?[transportError instanceof Error?transportError.message:String(transportError)]:[])])];
 return {status:full?'vintage_verified':'macro_incomplete',ruleVersion:MACRO_RULE,marketAsOf:cutoff,macro_vintage_date:asof,observedAt:timestamp,calculatedAt:timestamp,
  macro_stress:full?scoreRange[0]:null,categoryStates,decision,scoreRange,
  legacyStress:[real,policy,conditions,inflation].some(x=>x===null)?undefined:real!+policy!+conditions!+inflation!,
  categories:full?{rates:rates===1,conditions:conditions===1,inflation:inflation===1}:undefined,
  sourceError:problems.length?problems.join('；'):undefined,cpi_yoy:yoy,cpi_3m_annualized:annual,
  real_yield:last('DFII10')?.value??null,real_yield_90d_change:realDelta,fed_funds:last('FEDFUNDS')?.value??null,fed_funds_90d_change:policyDelta,
  financial_conditions:fc??null,financial_conditions_90d_change:fcDelta,...evidence(snapshots,asof,timestamp)
 };
}

export function unavailableMacro(cutoff:string,asof:string,raw:Record<string,string>,error:unknown,clock=new Date(),cpiVintage=asof):Macro{
 return calculateMacro(cutoff,asof,raw,clock,cpiVintage,error);
}
export function scoreMacro(cutoff:string,asof:string,raw:Record<string,string>,clock=new Date(),cpiVintage=asof):Macro{
 const result=calculateMacro(cutoff,asof,raw,clock,cpiVintage);
 if(result.sourceError)throw new Error(result.sourceError);
 return result;
}
