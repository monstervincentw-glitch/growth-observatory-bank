import type {Bar,Instrument} from './types.ts';

// Only a previously successful observation for the EXACT same closed session
// may be reused. Seed rows and earlier sessions do not satisfy this test.
export function sameCloseReusable(old:Instrument,cutoff:string):boolean{
 const transient=!old.sourceError||/個股收盤資料尚未到齊|來源HTTP (?:403|429|5\d\d)|fetch failed|timeout|timed out|aborted/i.test(old.sourceError);
 return old.priceDate===cutoff&&old.integrity===true&&Number.isFinite(old.close)&&old.close>0&&!!old.financialObservedAt&&Number.isFinite(Date.parse(old.financialObservedAt))&&transient;
}

export function dailyBoundsAgree(bar:Pick<Bar,'high'|'low'|'open'|'close'>):boolean{
 return Number.isFinite(bar.high)&&Number.isFinite(bar.low)&&bar.high!>=bar.low!&&
   bar.high!>=Math.max(bar.open,bar.close)*(1-1e-5)&&bar.low!<=Math.min(bar.open,bar.close)*(1+1e-5);
}

export function validateBars(input:Bar[]):{bars:Bar[];warnings:string[]}{
 const bars=[...input].sort((a,b)=>a.date.localeCompare(b.date));
 if(bars.length<252)throw new Error('有效行情不足252個交易日');
 const warnings:string[]=[];
 const ohlc=(b:Bar)=>[b.open,b.close,b.high,b.low,b.volume,b.adjClose].every(v=>typeof v==='number'&&Number.isFinite(v)&&v>0)&&dailyBoundsAgree(b);
 for(let n=0;n<bars.length;n++){
  const b=bars[n];
  if(!/^\d{4}-\d{2}-\d{2}$/.test(b.date)||!ohlc(b))throw new Error(`行情OHLC／成交量不完整或不一致：${b.date}`);
  if(n&&bars[n-1].date===b.date)throw new Error(`行情日期重複：${b.date}`);
  if(n>=Math.max(1,bars.length-126)){
   const previous=bars[n-1],adjusted=b.adjClose/previous.adjClose,raw=b.close/previous.close;
   if(Math.abs(adjusted-1)>.30){
    // A real large move is NOT a feed outage. Require internally consistent
    // traded OHLC and a stable adjustment factor; unexplained factor changes
    // remain blocked. Keep the move in the return series without smoothing.
    if(Math.abs(adjusted/raw-1)>.005)throw new Error(`還原價與成交價跳動不一致，待核對公司行動：${b.date}`);
    warnings.push(`${b.date}單日變動${((adjusted-1)*100).toFixed(2)}%；成交OHLC與還原係數一致，保留實際波動`);
   }
  }
 }
 return{bars,warnings};
}

export function requireClose(bars:Bar[],cutoff:string):Bar[]{
 if(bars.at(-1)?.date!==cutoff)throw new Error(`個股收盤資料尚未到齊：${bars.at(-1)?.date||'無有效資料'} / ${cutoff}`);
 return bars;
}

// Supplement missing TW sessions only when FinMind confirms the preceding raw
// close, no dividend/stock distribution occurred, and no unusual price move
// suggests an unhandled corporate action. Never invent an adjusted price.
export function backfillTaiwan(bars:Bar[],raw:any[],actions:any[],cutoff:string):Bar[]{
 const last=bars.at(-1);if(!last||last.date>=cutoff)return bars;
 const gap=(Date.parse(cutoff)-Date.parse(last.date))/86400000;
 if(gap>7||gap<0)throw new Error('備援缺口超過7天，無法安全銜接還原價格');
 if(actions.some(a=>a.date>last.date&&a.date<=cutoff))throw new Error('備援期間有除權息事件，等待完整還原資料');
 const preceding=raw.find(r=>r.date===last.date);
 if(!preceding||Math.abs(Number(preceding.close)/last.close-1)>.002)throw new Error('備援與主來源前一收盤價不一致');
 const factor=last.adjClose/last.close,extra:Bar[]=[];let prior=last;
 for(const r of raw.filter(r=>r.date>last.date&&r.date<=cutoff).sort((a,b)=>a.date.localeCompare(b.date))){
  if(Math.abs(Number(r.close)/prior.close-1)>.15)throw new Error('備援價格變動超過15%，待核對分割或公司行動');
  const b:Bar={date:r.date,time:Date.parse(r.date+'T09:00:00+08:00'),open:Number(r.open),close:Number(r.close),high:Number(r.max),low:Number(r.min),volume:Number(r.Trading_Volume),adjClose:Number(r.close)*factor,adjOpen:Number(r.open)*factor};
  extra.push(b);prior=b;
 }
 return requireClose(validateBars([...bars,...extra]).bars,cutoff);
}
