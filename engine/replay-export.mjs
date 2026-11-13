import {mkdir, readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {dateOffset, SERIES} from './macro.ts';
import {readMacroVintage, yahoo} from './providers.ts';

export const ETF_SYMBOLS = ['AGG','HYG','IEF','JNK','LQD','QQQ','SOXX','SPY','TLT','VOO','VT','XLK','SHY'];

async function save(folder, file, value) {
  await mkdir(folder, {recursive:true});
  await writeFile(join(folder,file), JSON.stringify(value)+'\n');
}

export async function archiveResearch(folder, {market, context, batch, bars, instruments, attempt}) {
  const {cutoff, benchmarks, macro, createdAt, marketDates}=context;
  const stocks=Object.fromEntries(instruments.map(s=>[s.symbol,{
    bars:bars[s.symbol]??[], source:s.priceSource, quoteStatus:s.priceValidation,
    financial:s.financial, financialObservedAt:s.financialObservedAt,
    financialSource:market==='US'?'SEC Company Facts':'FinMind',
    evaluation:s.evaluation, researchRank:s.researchRank, marketEvent:s.marketEvent,
    industry:s.industry, benchmark:s.benchmark, name:s.name, features:s.features
  }]));
  const verified=attempt.status==='published';
  const directory=verified?'primary-inputs':'unverified-primary-inputs';
  await save(join(folder,directory,market),cutoff+'.json',{
    schema:1, market, cutoff, batch, observedAt:createdAt, runId:attempt.runId,
    status:verified?'RESEARCH_VERIFIED':'RESEARCH_REJECTED', errors:attempt.errors,
    priceBasis:'adjusted total return', benchmarks, calendar:marketDates??benchmarks.QQQ.map(b=>b.date),
    stocks, macro
  });
}

// Compute the last observed session of each completed US week. A holiday
// Friday is eligible only after its regular reporting date has passed.
export function weeklySessions(bars, cutoff) {
  const byFriday=new Map();
  for (const {date} of bars) {
    if (date<='2026-11-06') continue;
    const weekday=new Date(date+'T12:00:00Z').getUTCDay();
    const friday=dateOffset(date,5-weekday);
    if (friday<=cutoff) byFriday.set(friday,date);
  }
  return [...byFriday.values()];
}

export async function archiveEtf(folder, context, batch, knownWeekly) {
  const {cutoff, benchmarks, createdAt}=context;
  const prices={}, errors={}, vintageErrors={};
  // Reuse the two benchmarks already returned by the research request.
  for (const symbol of ETF_SYMBOLS) {
    try {
      const rows=benchmarks[symbol]??await yahoo(symbol,'US',cutoff);
      if (rows.at(-1)?.date!==cutoff) throw Error('missing latest close');
      prices[symbol]=rows.slice(-320);
    } catch(error) { errors[symbol]=String(error.message||error).slice(0,160); }
  }
  const weeks=weeklySessions(benchmarks.QQQ,cutoff);
  for (const day of weeks) {
    if (knownWeekly.has(day)) continue;
    try {
      const series=Object.fromEntries(await Promise.all(SERIES.map(async s=>[s,await readMacroVintage(s,day)])));
      await save(join(folder,'weekly-vintages'),day+'.json',{day,source:'ALFRED',observedAt:new Date().toISOString(),series});
    } catch(error) { vintageErrors[day]=String(error.message||error).slice(0,160); }
  }
  await save(join(folder,'etf-inputs'),cutoff+'.json',{
    schema:1, cutoff, batch, observedAt:createdAt, updatedAt:new Date().toISOString(),
    source:'Yahoo Finance chart', priceBasis:'USD split/dividend-adjusted total return',
    status:Object.keys(errors).length?'PARTIAL_DATA':'VERIFIED', weeks, errors, vintageErrors, prices
  });
}

export async function knownWeeklyVintages(file) {
  if (!file) return new Set();
  return new Set((await readFile(file,'utf8')).trim().split(/\s+/));
}

if (process.argv[1] && new URL(import.meta.url).pathname===process.argv[1]) {
  const [contextPath, destination, indexPath] = process.argv.slice(2);
  const {context,batch}=JSON.parse(await readFile(contextPath,'utf8'));
  await archiveEtf(destination,context,batch,await knownWeeklyVintages(indexPath));
}
