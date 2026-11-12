import {MACRO_RULE,decideFunding} from './macro.ts';
import type {Instrument,Macro,Evaluation,Financial} from './types.ts';
export const finite=(v:unknown):v is number=>typeof v==='number'&&Number.isFinite(v);
export const days=(a:string,b:string)=>Math.floor((Date.parse(a.slice(0,10)+'T12:00:00Z')-Date.parse(b.slice(0,10)+'T12:00:00Z'))/86400000);
export function fundamentalReasons(f:Financial,baseline?:Record<string,unknown>):string[]{
 const r:string[]=[];
 if(f.financial_status!=='ok'||![f.revenue_growth,f.quarterly_growth,f.operating_margin,f.cfo_margin].every(finite))return ['財報欄位不足，暫停判斷'];
 if(!finite(f.financial_age_days)||f.financial_age_days>180)r.push('財報距季末超過180天');
 if(f.revenue_growth!<.10)r.push('TTM營收成長低於10%');
 if(f.quarterly_growth!<0)r.push('最新單季營收年減');
 if(f.operating_margin!<=0)r.push('營業利益率不為正');
 if(f.cfo_margin!<=0)r.push('營業現金流率不為正');
 if(finite(f.prior_release_growth)&&f.revenue_growth!<=f.prior_release_growth-.10+1e-12)r.push('營收成長較前次財報少10個百分點');
 if(finite(f.prior_release_margin)&&f.operating_margin!<=f.prior_release_margin-.05+1e-12)r.push('營益率較前次財報少5個百分點');
 if(baseline){
  if(finite(baseline.revenue_growth)&&f.revenue_growth!<=baseline.revenue_growth-.10+1e-12)r.push('營收成長較首次登錄少10個百分點');
  if(finite(baseline.operating_margin)&&f.operating_margin!<=baseline.operating_margin-.05+1e-12)r.push('營益率較首次登錄少5個百分點');
 }
 return r;
}
export function currentMacro(m:Macro,now=new Date()){
 return m.ruleVersion===MACRO_RULE&&['vintage_verified','macro_incomplete'].includes(m.status)&&!!m.marketAsOf&&days(now.toISOString(),m.marketAsOf)>=0&&days(now.toISOString(),m.marketAsOf)<=5;
}
export function macroVerified(m:Macro,now=new Date()){
 return currentMacro(m,now)&&m.status==='vintage_verified'&&finite(m.macro_stress)&&Number.isInteger(m.macro_stress)&&m.macro_stress>=0&&m.macro_stress<=3;
}
export function macroDecision(m:Macro,now=new Date()){
 if(!currentMacro(m,now))return 'unknown';
 if(m.categoryStates&&Object.values(m.categoryStates).length===3&&Object.values(m.categoryStates).every(v=>v===0||v===1||v===null))
  return decideFunding(m.categoryStates).decision;
 return macroVerified(m,now)?m.macro_stress!>=2?'red':'non_red':'unknown';
}
export function verifiedFiling(i:Instrument){
 if(i.market==='US')return true;
 const f=i.financial,e=f.filingEvidence;
 const close=Date.parse(i.priceDate+'T13:30:00+08:00');
 return !!e&&e.period===f.latest_period&&e.releaseKey===f.fundamental_release_key&&Date.parse(e.publishedAt)<=close&&Date.parse(e.verifiedThrough)>=close&&/^https:\/\//.test(e.source);
}
export function evaluate(i:Instrument,m:Macro,now=new Date()):Evaluation {
 const errors=fundamentalReasons(i.financial);const fundamental=errors.length===0;
 const verified=macroVerified(m,now);
 const decision=macroDecision(m,now);
 const funding=decision==='non_red';
 const filingVerified=verifiedFiling(i);
 const complete=m.marketComplete!==false;
 const legacyFunding=verified&&finite(m.legacyStress)&&m.legacyStress<=1;
 const divergence=finite(i.features.drawdown_126)&&i.features.drawdown_126>=-.50&&i.features.drawdown_126<=-.10&&i.features.relative_63<0;
 const tradeChecks=[
  {ok:i.features.history_sessions>=252,reason:'有效行情少於 252 個交易日'},
  {ok:i.features.dollar_volume>=(i.market==='US'?1e7:3e8),reason:i.market==='US'?'20 日平均成交額未達 1,000 萬美元':'20 日平均成交額未達 3 億新台幣'},
 ];
 if(i.market==='US')tradeChecks.push(
  {ok:finite(i.features.market_cap_estimate)&&i.features.market_cap_estimate>=1e9,reason:'市值未達 10 億美元或缺資料'},
  {ok:finite(i.features.price_sales)&&i.features.price_sales>=.05&&i.features.price_sales<=200,reason:'P/S 不在 0.05–200 或缺資料'},
  {ok:finite(i.financial.growth_acceleration),reason:'缺少營收成長加速度資料'},
  {ok:!!i.financial.shares_date&&days(i.priceDate,i.financial.shares_date)<=180,reason:'股數資料缺漏或距行情逾 180 天'},
 );
 const tradable=tradeChecks.every(check=>check.ok);
 const fresh=researchObservation(i,m.marketAsOf||'')&&days(now.toISOString(),i.priceDate)>=0&&days(now.toISOString(),i.priceDate)<=5;
 const reasons=[...errors,...(!filingVerified?['缺少官方財報申報時間、當時版本及最新申報核對，正式訊號待確認']:[]),...(!funding?[decision==='red'?'全球資金紅燈':'全球資金判定待確認']:[]),...tradeChecks.filter(check=>!check.ok).map(check=>check.reason),...(!complete?['本市場快照未完整發布，暫停正式訊號']:[]),...(!fresh?['資料待更新或來源異常']:[])];
 return {fundamental,filingVerified,funding,tradable,divergence,fresh,primary:complete&&filingVerified&&fundamental&&funding&&tradable&&fresh,buy_hold:complete&&filingVerified&&fundamental&&legacyFunding&&tradable&&fresh&&divergence,reasons,fundamentalReasons:errors};
}
function ranks(values:{key:string,value:number}[]):Map<string,number>{
 const sorted=[...values].sort((a,b)=>a.value-b.value);const out=new Map<string,number>();
 for(let n=0;n<sorted.length;){let e=n+1;while(e<sorted.length&&sorted[e].value===sorted[n].value)e++;const v=((n+1+e)/2)/sorted.length;for(let j=n;j<e;j++)out.set(sorted[j].key,v);n=e;}return out;
}
export function researchObservation(i:Instrument,date:string){
 return i.integrity&&!i.sourceError&&!i.marketEvent&&i.priceDate===date&&finite(i.close)&&i.close>0;
}
const US_FUNDAMENTAL_WEIGHTS={growth:.45,acceleration:.20,operating:.20,cashflow:.15} as const;
const US_FUNDAMENTAL_SHARE=.75;
const US_VALUATION_WEIGHT=.25;
export const RANK_WEIGHTS={
 US:{growth:US_FUNDAMENTAL_SHARE*US_FUNDAMENTAL_WEIGHTS.growth,acceleration:US_FUNDAMENTAL_SHARE*US_FUNDAMENTAL_WEIGHTS.acceleration,
  operating:US_FUNDAMENTAL_SHARE*US_FUNDAMENTAL_WEIGHTS.operating,cashflow:US_FUNDAMENTAL_SHARE*US_FUNDAMENTAL_WEIGHTS.cashflow,value:US_VALUATION_WEIGHT},
 TW:{growth:.5,acceleration:0,operating:.25,cashflow:.25,value:0},
} as const;
export type RankFactor=keyof typeof RANK_WEIGHTS.US;
export type RankDetail={parts:{key:RankFactor;value:number;percentile:number;weight:number}[];poolSize:number;industrySize:number;financialPeriod:string|null;filingDate:string|null;financialObservedAt:string|null};

export function rankInstruments(instruments:Instrument[],macro:Macro,researchDate?:string){
 const pool=instruments.filter(i=>{
  const e=evaluate(i,macro);
  return e.fundamental&&e.tradable&&(researchDate?researchObservation(i,researchDate):e.fresh&&(i.market==='US'||e.funding));
 });
 const getters={
  growth:(i:Instrument)=>i.financial.revenue_growth!,
  acceleration:(i:Instrument)=>i.financial.growth_acceleration??0,
  operating:(i:Instrument)=>i.financial.operating_margin!,
  cashflow:(i:Instrument)=>i.financial.cfo_margin!,
  value:(i:Instrument)=>i.features.price_sales??0,
 };
 const rank=(key:RankFactor)=>ranks(pool.map(i=>({key:i.key,value:getters[key](i)})));
 const byIndustry=new Map<string,Instrument[]>();
 for(const i of pool)byIndustry.set(i.industry,[...(byIndustry.get(i.industry)||[]),i]);
 const value=new Map<string,number>();
 for(const list of byIndustry.values()){
  const rr=ranks(list.map(i=>({key:i.key,value:-getters.value(i)})));
  for(const i of list)value.set(i.key,list.length>=3?rr.get(i.key)!:.5);
 }
 const ranked={growth:rank('growth'),acceleration:rank('acceleration'),operating:rank('operating'),cashflow:rank('cashflow'),value};
 return instruments.map(i=>{
  if(!ranked.growth.has(i.key))return {...i,rankScore:null,rankDetail:null};
  const g=ranked.growth.get(i.key)!,a=ranked.acceleration.get(i.key)!,op=ranked.operating.get(i.key)!,cf=ranked.cashflow.get(i.key)!;
  const w=US_FUNDAMENTAL_WEIGHTS;
  const rankScore=100*(i.market==='TW'?RANK_WEIGHTS.TW.growth*g+RANK_WEIGHTS.TW.operating*op+RANK_WEIGHTS.TW.cashflow*cf:
   US_FUNDAMENTAL_SHARE*(w.growth*g+w.acceleration*a+w.operating*op+w.cashflow*cf)+US_VALUATION_WEIGHT*value.get(i.key)!);
  const parts=(Object.keys(ranked) as RankFactor[]).filter(key=>RANK_WEIGHTS[i.market][key]>0)
   .map(key=>({key,value:getters[key](i),percentile:ranked[key].get(i.key)!,weight:RANK_WEIGHTS[i.market][key]}));
  const rankDetail:RankDetail={parts,poolSize:pool.length,industrySize:byIndustry.get(i.industry)!.length,
   financialPeriod:i.financial.latest_period??null,filingDate:i.financial.filingEvidence?.publishedAt??i.financial.max_input_filed??null,
   financialObservedAt:i.financialObservedAt??null};
  return {...i,rankScore,rankDetail};
 });
}
