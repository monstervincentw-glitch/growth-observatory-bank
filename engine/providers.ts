import {monitoredFetch} from './update-diagnostics.mjs';
type Diagnostic = (code:number|string,provider?:string)=>void;
import {macroCutoff,latestCpiVintage,scoreMacro,unavailableMacro,SERIES,type MacroSeriesInput} from './macro.ts';
export type {MacroSeriesInput} from './macro.ts';
import type {Instrument,Financial,Market,Macro,Bar} from './types.ts';
import {finite,days} from './rules.ts';
import {sameCloseReusable,validateBars,requireClose,backfillTaiwan} from './quality.ts';
const dayms=86400000;
const iso=(n:number)=>new Date(n).toISOString().slice(0,10);
const ms=(d:string)=>Date.parse(d.slice(0,10)+'T12:00:00Z');
const sum=(v:number[])=>v.reduce((a,b)=>a+b,0);
const mean=(v:number[])=>v.length?sum(v)/v.length:NaN;
export const latestClosedDate=(market:Market,now=new Date())=>{
 const tz=market==='US'?'America/New_York':'Asia/Taipei';const parts=new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',hourCycle:'h23'}).formatToParts(now);
 const val=(t:string)=>parts.find(p=>p.type===t)!.value;const d=`${val('year')}-${val('month')}-${val('day')}`;
 let eligible=Number(val('hour'))>=(market==='US'?17:16)?d:iso(ms(d)-dayms);
 while([0,6].includes(new Date(ms(eligible)).getUTCDay()))eligible=iso(ms(eligible)-dayms);
 return eligible;
};
async function readJSON(url:string,headers:Record<string,string>={},diagnostic?:Diagnostic){
 const response=await monitoredFetch(url,{headers:{'User-Agent':'Mozilla/5.0 GrowthObservatory/1.0 personal research',...headers},signal:AbortSignal.timeout(22000)},diagnostic);
 if(!response.ok)throw new Error(`來源HTTP ${response.status}: ${new URL(url).hostname}${response.status===429?'（來源限流，本次不連續重試）':''}`);
 return response.json() as Promise<any>;
}
export async function yahoo(ticker:string,market:Market,cutoff:string,diagnostic?:Diagnostic):Promise<Bar[]>{
 const end=Math.floor((ms(cutoff)+2*dayms)/1000),start=Math.floor((ms(cutoff)-850*dayms)/1000);
 const obj=await readJSON(`https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(ticker)}?period1=${start}&period2=${end}&interval=1d&events=div%2Csplits`,{},diagnostic);
 const x=obj.chart?.result?.[0];if(!x)throw new Error('行情來源沒有有效結果');
 const q=x.indicators.quote[0],adj=x.indicators.adjclose?.[0]?.adjclose;
 const zone=market==='US'?'America/New_York':'Asia/Taipei';
 const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:zone,year:'numeric',month:'2-digit',day:'2-digit'});
 const rows:Bar[]=[];
 for(let k=0;k<x.timestamp.length;k++){
  const p=fmt.formatToParts(new Date(x.timestamp[k]*1000));const val=(t:string)=>p.find(v=>v.type===t)!.value;const d=`${val('year')}-${val('month')}-${val('day')}`;
  if(d>cutoff||!(q.open[k]>0&&q.close[k]>0&&adj?.[k]>0&&q.volume[k]>0))continue;
  rows.push({date:d,time:x.timestamp[k]*1000,open:q.open[k],close:q.close[k],adjClose:adj[k],adjOpen:q.open[k]*adj[k]/q.close[k],volume:q.volume[k],high:q.high[k],low:q.low[k]});
 }
 return validateBars(rows).bars;
}
export async function finmind(dataset:string,symbol?:string,start='2018-01-01',end=iso(Date.now()),diagnostic?:Diagnostic){
 const q=new URLSearchParams({dataset,start_date:start,end_date:end});if(symbol)q.set('data_id',symbol);
 const obj=await readJSON('https://api.finmindtrade.com/api/v4/data?'+q,{},diagnostic);
 if(obj.status!==200||!Array.isArray(obj.data))throw new Error(`FinMind ${dataset}: ${obj.msg||'來源格式異常'}`);
 return obj.data as any[];
}
async function finishRequests<T extends readonly unknown[]>(requests:T):Promise<{[K in keyof T]:Awaited<T[K]>}>{
 const results=await Promise.allSettled(requests);
 const failed=results.find(r=>r.status==='rejected');
 if(failed)throw failed.reason;
 return results.map(r=>(r as PromiseFulfilledResult<unknown>).value) as {[K in keyof T]:Awaited<T[K]>};
}
export async function taiwanBenchmark(cutoff:string,diagnostic?:Diagnostic):Promise<{bars:Bar[],dates:string[]}>{
 const start=iso(ms(cutoff)-850*dayms);
 const [prices,divs,calendar]=await finishRequests([finmind('TaiwanStockPrice','0052',start,cutoff,diagnostic),finmind('TaiwanStockDividendResult','0052',start,cutoff,diagnostic),finmind('TaiwanStockTradingDate',undefined,start,cutoff,diagnostic)] as const);
 const dividends=new Map<string,number>();for(const r of divs)dividends.set(r.date,(dividends.get(r.date)||0)+Number(r.stock_and_cache_dividend));
 const rows=prices.filter(r=>r.open>0&&r.close>0&&r.Trading_Volume>0).sort((a,b)=>a.date.localeCompare(b.date));
 let wealth=1;const result:Bar[]=[];
 for(let n=0;n<rows.length;n++){
  const r=rows[n];const split=r.date==='2025-11-26'?7:1;
  if(n){const change=(r.close*split+(dividends.get(r.date)||0))/rows[n-1].close;if(Math.abs(change-1)>.20)throw new Error('0052可能有新的分割／行情異常，暫停背離訊號');wealth*=change;}
  result.push({date:r.date,time:Date.parse(r.date+'T09:00:00+08:00'),open:r.open,close:r.close,adjClose:wealth,adjOpen:wealth*r.open/r.close,volume:r.Trading_Volume});
 }
 if(result.length<252)throw new Error('0052有效歷史不足');
 return {bars:result,dates:calendar.map(r=>r.date).filter((d:string)=>d>=start&&d<=cutoff)};
}
const tags:Record<string,string[]>={revenue:['RevenueFromContractWithCustomerExcludingAssessedTax','Revenues','SalesRevenueNet','SalesRevenueGoodsNet','RevenueFromContractWithCustomerIncludingAssessedTax'],op:['OperatingIncomeLoss'],cfo:['NetCashProvidedByUsedInOperatingActivities'],shares:['CommonStockSharesOutstanding','EntityCommonStockSharesOutstanding']};
interface Fact {start?:string;end:string;filed:string;val:number;priority:number;days?:number;derived?:boolean;}
function knownFacts(payload:any,metric:string,asof:string):Fact[]{
 const cutoff=iso(ms(asof)-2*dayms);let rows:Fact[]=[];
 for(const [priority,tag] of tags[metric].entries())for(const taxonomy of ['us-gaap','dei','ifrs-full']){
  const a=payload.facts?.[taxonomy]?.[tag]?.units?.[metric==='shares'?'shares':'USD']||[];
  rows.push(...a.filter((r:any)=>['10-K','10-K/A','10-Q','10-Q/A','20-F','20-F/A','40-F','6-K'].includes(r.form)&&r.filed&&r.end&&finite(r.val)&&r.filed<=cutoff&&r.end<=asof&&ms(r.end)>=ms(asof)-1100*dayms).map((r:any)=>({...r,priority})));
 }
 rows.sort((a,b)=>b.filed.localeCompare(a.filed)||a.priority-b.priority);
 const used=new Set<string>();return rows.filter(r=>{const key=(r.start||'')+'|'+r.end;if(used.has(key))return false;used.add(key);return true;});
}
function quarters(payload:any,metric:string,asof:string):Fact[]{
 const known=knownFacts(payload,metric,asof).filter(r=>r.start).map(r=>({...r,days:Math.round((ms(r.end)-ms(r.start!))/dayms)+1}));const q:Fact[]=[];
 for(const r of known){
  if(r.days!>=60&&r.days!<=120)q.push({...r,derived:false});
  else if(r.days!>=140&&r.days!<=400){
   const previous=known.filter(p=>Math.abs(ms(p.start!)-ms(r.start!))<=3*dayms&&ms(r.end)-ms(p.end)>=60*dayms&&ms(r.end)-ms(p.end)<=120*dayms&&p.days!<r.days!).sort((a,b)=>b.end.localeCompare(a.end)||b.filed.localeCompare(a.filed))[0];
   if(previous)q.push({...r,start:iso(ms(previous.end)+dayms),val:r.val-previous.val,filed:r.filed>previous.filed?r.filed:previous.filed,derived:true});
  }
 }
 q.sort((a,b)=>a.end.localeCompare(b.end)||b.filed.localeCompare(a.filed)||Number(a.derived)-Number(b.derived));const used=new Set<string>();return q.filter(r=>{if(used.has(r.end))return false;used.add(r.end);return true;});
}
export function secFinancial(payload:any,asof:string):Financial{
 const revenue=quarters(payload,'revenue',asof).slice(-8);
 const invalid:Financial={financial_status:'insufficient_quarterly_revenue',revenue_growth:null,quarterly_growth:null,operating_margin:null,cfo_margin:null,revenue_ttm:null,financial_age_days:null,fundamental_release_key:null};
 if(revenue.length!==8||revenue.some(r=>r.val<=0)||revenue.some((r,k)=>k&&Math.abs(ms(r.end)-ms(revenue[k-1].end))<60*dayms)||revenue.some((r,k)=>k&&ms(r.end)-ms(revenue[k-1].end)>120*dayms))return invalid;
 const latest=revenue.slice(-4),ends=new Set(latest.map(r=>r.end));const total=sum(latest.map(r=>r.val)),prior=sum(revenue.slice(0,4).map(r=>r.val));
 const op=quarters(payload,'op',asof).filter(r=>ends.has(r.end)),cf=quarters(payload,'cfo',asof).filter(r=>ends.has(r.end));
 const shares=knownFacts(payload,'shares',asof).sort((a,b)=>b.end.localeCompare(a.end)||b.filed.localeCompare(a.filed)||a.priority-b.priority)[0];
 const used=[...revenue,...op,...cf,...(shares?[shares]:[])];
 const f:Financial={financial_status:op.length===4&&cf.length===4?'ok':'incomplete_financial_fields',revenue_growth:total/prior-1,quarterly_growth:revenue[7].val/revenue[3].val-1,
  growth_acceleration:revenue[7].val/revenue[3].val-revenue[6].val/revenue[2].val,operating_margin:op.length===4?sum(op.map(r=>r.val))/total:null,cfo_margin:cf.length===4?sum(cf.map(r=>r.val))/total:null,revenue_ttm:total,
  shares:shares?.val??null,shares_date:shares?.end??null,shares_filed:shares?.filed??null,latest_period:revenue[7].end,max_input_filed:used.map(r=>r.filed).sort().at(-1)||null,
  financial_age_days:days(asof,revenue[7].end),fundamental_release_key:null,financial_event_date:used.map(r=>r.filed).sort().at(-1)||null,date_basis:'SEC實際申報日加2天'};
 f.fundamental_release_key=[f.latest_period,f.revenue_growth,f.operating_margin,f.cfo_margin].join('|');return f;
}
export function twFinancial(income:any[],cashflow:any[],asof:string):Financial{
 const pivot=(rows:any[])=>{const out=new Map<string,Record<string,number>>();for(const r of rows){if(r.date>asof)continue;out.set(r.date,{...(out.get(r.date)||{}),[r.type]:r.value===null?NaN:Number(r.value)});}return out;};
 const inc=pivot(income),cf=pivot(cashflow);const dates=[...inc.keys()].sort();
 const value=(map:Map<string,Record<string,number>>,date:string,keys:string[])=>{for(const k of keys){const v=map.get(date)?.[k];if(finite(v))return v;}return NaN;};
 const rows=dates.map(date=>{const year=date.slice(0,4),month=Number(date.slice(5,7));const q=Math.ceil(month/3);const previous=q===1?null:dates.find(d=>d.slice(0,4)===year&&Math.ceil(Number(d.slice(5,7))/3)===q-1);
  const c=value(cf,date,['CashFlowsFromOperatingActivities','NetCashInflowFromOperatingActivities']);const pc=previous?value(cf,previous,['CashFlowsFromOperatingActivities','NetCashInflowFromOperatingActivities']):NaN;
  return{date,revenue:value(inc,date,['Revenue','OperatingRevenue']),op:value(inc,date,['OperatingIncome','OperatingProfit']),cfo:q===1?c:c-pc};});
 const at=(end:number)=>{const a=rows.slice(end-3,end+1);return a.length===4&&a.every((r,k)=>finite(r.revenue)&&r.revenue>0&&(!k||days(r.date,a[k-1].date)>=60&&days(r.date,a[k-1].date)<=120))?a:null;};
 const calc=(end:number)=>{const a=at(end),b=at(end-4);if(!a||!b)return null;const rev=sum(a.map(r=>r.revenue));return{growth:rev/sum(b.map(r=>r.revenue))-1,quarter:a[3].revenue/b[3].revenue-1,op:a.every(r=>finite(r.op))?sum(a.map(r=>r.op))/rev:NaN,cfo:a.every(r=>finite(r.cfo))?sum(a.map(r=>r.cfo))/rev:NaN,revenue:rev};};
 const n=rows.length-1,last=rows.at(-1),x=calc(n),prior=calc(n-1);
 const f:Financial={financial_status:x&&[x.growth,x.quarter,x.op,x.cfo].every(finite)?'ok':'incomplete_financial_fields',revenue_growth:x?.growth??null,quarterly_growth:x?.quarter??null,operating_margin:finite(x?.op)?x!.op:null,cfo_margin:finite(x?.cfo)?x!.cfo:null,revenue_ttm:x?.revenue??null,latest_period:last?.date??null,financial_age_days:last?days(asof,last.date):null,prior_release_growth:prior?.growth??null,prior_release_margin:finite(prior?.op)?prior!.op:null,fundamental_release_key:null,financial_event_date:null,date_basis:'啟用後實際首次觀測時間，非回填歷史公告日'};
 f.fundamental_release_key=[f.latest_period,f.revenue_growth,f.operating_margin,f.cfo_margin].join('|');return f;
}
export async function readMacroVintage(series:typeof SERIES[number],vintage:string,diagnostic?:Diagnostic){
 const url=`https://alfred.stlouisfed.org/graph/alfredgraph.csv?id=${series}&vintage_date=${vintage}&cosd=2019-01-01`;
 const response=await monitoredFetch(url,{headers:{'User-Agent':'GrowthObservatory research'},signal:AbortSignal.timeout(18000)},diagnostic);
 if(!response.ok)throw new Error(series+' 總經來源 HTTP '+response.status);
 return response.text();
}
export async function refreshMacro(cutoff:string,supplied?:MacroSeriesInput,market:Market='US',allowUnavailable=false,diagnostic?:Diagnostic):Promise<Macro>{
 const asof=macroCutoff(cutoff),cpiVintage=latestCpiVintage(asof,cutoff,market==='US');
 try{
  // An older transport payload cannot override a fresh request for this close.
  let raw:Record<string,string>,transportError:Error|undefined;
  if(supplied?.vintageDate===asof&&(supplied.cpiVintageDate??asof)===cpiVintage){
   raw=supplied.series;
  }else{
   const results=await Promise.allSettled(SERIES.map(async series=>
    [series,await readMacroVintage(series,series==='CPIAUCSL'?cpiVintage:asof,diagnostic)] as const));
   const rejected=results.filter(r=>r.status==='rejected');
   if(rejected.length){
    transportError=new Error(rejected.map(r=>String(r.reason)).join('；'));
    if(!allowUnavailable)throw transportError;
   }
   raw=Object.fromEntries(results.flatMap(r=>r.status==='fulfilled'?[r.value]:[]));
  }
  if(transportError)return unavailableMacro(cutoff,asof,raw,transportError,new Date(),cpiVintage);
  try{return scoreMacro(cutoff,asof,raw,new Date(),cpiVintage);}
  catch(error){return unavailableMacro(cutoff,asof,raw,error,new Date(),cpiVintage);}
 }catch(error){throw Object.assign(error instanceof Error?error:new Error(String(error)),{requiredMacroVintageDate:asof,requiredCpiVintageDate:cpiVintage,marketCloseDate:cutoff});}
}
export interface RefreshContext{market:Market;cutoff:string;benchmarks:Record<string,Bar[]>;marketDates?:string[];macro:Macro;createdAt:string;}
export async function makeContext(market:Market,supplied?:MacroSeriesInput,allowMacroUnavailable=false,diagnostic?:(type:string,symbol:string|null)=>Diagnostic):Promise<RefreshContext>{
 const closed=latestClosedDate(market);const benchmarks:Record<string,Bar[]>={};let cutoff=closed,marketDates:string[]|undefined;
 if(market==='US'){for(const name of ['QQQ','SOXX','IGV'])benchmarks[name]=await yahoo(name,'US',closed,diagnostic?.('benchmark',name));cutoff=benchmarks.QQQ.at(-1)!.date;}
 else{const b=await taiwanBenchmark(closed,diagnostic?.('benchmark','0052'));benchmarks['0052']=b.bars;marketDates=b.dates;cutoff=b.dates.at(-1)!;}
 if(!cutoff||days(closed,cutoff)>5)throw new Error('市場交易日曆或基準行情過期');
 const macro=await refreshMacro(cutoff,supplied,market,allowMacroUnavailable,diagnostic?.('macro',null));return{market,cutoff,benchmarks,marketDates,macro,createdAt:new Date().toISOString()};
}
export function priceFeatures(bars:Bar[],benchmark:Bar[]){
 const bmap=new Map(benchmark.map(b=>[b.date,b.adjClose]));const ref=(date:string)=>benchmark.filter(b=>b.date<=date).at(-1)?.adjClose??NaN;
 const tail=bars.slice(-126),p=bars.at(-1)!,previous=bars.at(-64)!;
 const benchmarkReturn=ref(p.date)/ref(previous.date)-1;
 return{history_sessions:bars.length,dollar_volume:mean(bars.slice(-20).map(b=>b.close*b.volume)),drawdown_126:p.adjClose/Math.max(...tail.map(b=>b.adjClose))-1,relative_63:p.adjClose/previous.adjClose-1-benchmarkReturn};
}
const confirmedMarketEvents:Record<string,{effectiveDate:string;lastTradeDate:string;lastClose:number;summary:string;source:string}> = {
 'US:ROKU':{effectiveDate:'2026-11-09',lastTradeDate:'2026-11-06',lastClose:160.52,
  summary:'併購完成，2026-11-09 起停止交易。原持股需按券商確認的現金及換股交割處理。',
  source:'https://www.sec.gov/Archives/edgar/data/1428439/000119312526451874/d181620d8k.htm'}
};

export function confirmedInactive(instrument:Instrument,cutoff:string):Instrument|null{
 const event=confirmedMarketEvents[instrument.key];
 if(!event||cutoff<event.effectiveDate)return null;
 const {effectiveDate,lastTradeDate,lastClose,summary,source}=event;
 return {...instrument,priceDate:lastTradeDate,close:lastClose,integrity:true,sourceError:null,rankScore:null,
  priceSource:'Yahoo／FinMind 最後成交核對',sourceWarning:summary,marketEvent:{effectiveDate,summary,source}};
}

export async function refreshInstrument(old:Instrument,context:RefreshContext,reuseFinancial=false,cachedBars:Bar[]=[],diagnostic?:(type:string)=>Diagnostic):Promise<Instrument & {_bars?:Bar[]}>{
 const inactive=confirmedInactive(old,context.cutoff);if(inactive)return inactive;
 if(context.cutoff<old.priceDate)throw new Error(`更新截止日${context.cutoff}落後既有收盤${old.priceDate}，拒絕覆寫較新的資料`);
 if(sameCloseReusable(old,context.cutoff))return{...old,sourceError:null,lastAttemptAt:new Date().toISOString(),sourceWarning:old.sourceError?`保留同一收盤日已成功驗證快照；前次重抓異常：${old.sourceError}`:old.sourceWarning,priceValidation:{cutoff:context.cutoff,validatedAt:old.priceValidation?.validatedAt||old.observedAt,mode:'same_close_snapshot'}};
 let bars:Bar[]=[],priceSource='Yahoo',primaryError='';
 try{bars=await yahoo(old.ticker,old.market,context.cutoff,diagnostic?.('quote'));}catch(e){primaryError=e instanceof Error?e.message:String(e);}
 if(primaryError||bars.at(-1)?.date!==context.cutoff){
  primaryError=primaryError||`主來源最後完整日期${bars.at(-1)?.date||'無'}，目標${context.cutoff}`;
  try{
   const start=iso(ms(context.cutoff)-850*dayms);
   if(old.market==='US'){
    const raw=await finmind('USStockPrice',old.symbol,start,context.cutoff,diagnostic?.('quote'));
    bars=raw.filter(r=>r.date<=context.cutoff).map(r=>({date:r.date,time:ms(r.date),open:Number(r.Open),close:Number(r.Close),high:Number(r.High),low:Number(r.Low),volume:Number(r.Volume),adjClose:Number(r.Adj_Close),adjOpen:Number(r.Open)*Number(r.Adj_Close)/Number(r.Close)}));
    bars=requireClose(validateBars(bars).bars,context.cutoff);priceSource='FinMind USStockPrice';
   }else{
    const history=bars.length?bars:cachedBars;
    const last=history.at(-1);if(!last)throw new Error('尚無已驗證歷史可供備援銜接');
    const [raw,actions]=await finishRequests([finmind('TaiwanStockPrice',old.symbol,last.date,context.cutoff,diagnostic?.('quote')),finmind('TaiwanStockDividendResult',old.symbol,last.date,context.cutoff,diagnostic?.('quote'))] as const);
    bars=backfillTaiwan(history,raw,actions,context.cutoff);priceSource='Yahoo歷史＋FinMind已核對收盤';
   }
  }catch(e){throw new Error(`${primaryError}；備援未通過：${e instanceof Error?e.message:String(e)}`);}
 }
 if(context.marketDates){const valid=new Set(context.marketDates);bars=bars.filter(b=>valid.has(b.date));}
 const validated=validateBars(bars);bars=requireClose(validated.bars,context.cutoff);
 let financial:Financial={...old.financial};
 if(!reuseFinancial){
  try{
  if(old.market==='US')financial=secFinancial(await readJSON(`https://data.sec.gov/api/xbrl/companyfacts/CIK${String(old.cik).padStart(10,'0')}.json`,{'User-Agent':'GrowthObservatory/1.0 personal financial research'},diagnostic?.('financial')),bars.at(-1)!.date);
  else{const start=iso(ms(context.cutoff)-1400*dayms);const [income,cash]=await finishRequests([finmind('TaiwanStockFinancialStatements',old.symbol,start,context.cutoff,diagnostic?.('financial')),finmind('TaiwanStockCashFlowsStatement',old.symbol,start,context.cutoff,diagnostic?.('financial'))] as const);financial=twFinancial(income,cash,bars.at(-1)!.date);}
  }catch(error){throw Object.assign(new Error('財報來源：'+(error instanceof Error?error.message:String(error))),{dataType:'financial'});}
 }
 if(old.market==='TW'&&old.financial.filingEvidence?.releaseKey===financial.fundamental_release_key)
  financial.filingEvidence=old.financial.filingEvidence;
 const same=financial.latest_period===old.financial.latest_period&&['revenue_growth','operating_margin','cfo_margin'].every(k=>{const a=(financial as any)[k],b=(old.financial as any)[k];return a===b||(finite(a)&&finite(b)&&Math.abs(a-b)<1e-9);});
 if(old.market==='US'){financial.prior_release_growth=same?old.financial.prior_release_growth:old.financial.revenue_growth;financial.prior_release_margin=same?old.financial.prior_release_margin:old.financial.operating_margin;}
 financial.financial_age_days=financial.latest_period?days(bars.at(-1)!.date,financial.latest_period):null;
 const features:{[key:string]:any}=priceFeatures(bars,context.benchmarks[old.benchmark]||context.benchmarks[old.market==='US'?'QQQ':'0052']);
 if(old.market==='US'){features.market_cap_estimate=finite(financial.shares)?financial.shares*bars.at(-1)!.close:null;features.price_sales=finite(features.market_cap_estimate)&&financial.revenue_ttm?features.market_cap_estimate/financial.revenue_ttm:null;}
 const stamp=new Date().toISOString();
 const result={...old,priceDate:bars.at(-1)!.date,close:bars.at(-1)!.close,history:bars.slice(-90).map(b=>({date:b.date,close:b.adjClose})),features:features as any,financial,observedAt:stamp,financialObservedAt:reuseFinancial?old.financialObservedAt:stamp,sourceError:null,integrity:true,priceSource,sourceWarning:[...(primaryError?[`主來源未完整，已由備援核對：${primaryError}`]:[]),...validated.warnings].join('；')||null,lastAttemptAt:stamp,priceValidation:{cutoff:context.cutoff,validatedAt:stamp,mode:priceSource==='Yahoo'?'source_verified':'fallback_verified'},_bars:bars.slice(-320)};
 if(result.priceDate!==context.cutoff)throw new Error(`個股收盤資料尚未到齊：${result.priceDate} / ${context.cutoff}`);
 return result;
}
