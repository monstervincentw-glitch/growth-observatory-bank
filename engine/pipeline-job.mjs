// Daily transport adapter. Strategy calculations remain in the existing engine.
import {readFile, writeFile} from 'node:fs/promises';
import {yahoo, refreshMacro, refreshInstrument, priceFeatures, confirmedInactive} from './providers.ts';
import {evaluate, rankInstruments} from './rules.ts';
import {project, envelope} from './project.mjs';
import {verifyMarketInputs, requirePublishable} from './publication.mjs';

export function checkQuotes(input, before) {
  const {market, batch} = input;
  if (batch.market !== market || batch.status !== 'REFERENCE_QUOTES_COMPLETE' || batch.missing.length)
    throw Error('Google 行情批次尚未完整');
  const symbols = new Set(before.stocks.map(s => s.symbol));
  if (batch.expected !== symbols.size || Object.keys(batch.quotes).length + batch.inactive.length !== symbols.size)
    throw Error('Google 行情股票池不符');
  for (const [symbol, q] of Object.entries(batch.quotes)) {
    if (!symbols.has(symbol) || q.priceDate !== batch.day || !Number.isFinite(q.close) || q.close <= 0 ||
        q.currency !== (market === 'TW' ? 'TWD' : 'USD')) throw Error('Google 行情代號、日期、幣別不符');
  }
  if (batch.day < before.macro.marketAsOf) throw Error('禁止行情日期倒退');
}

export async function calculate(input, progress = () => {}) {
  const {market, batch, prior, runtime} = input;
  const before = prior.snapshot.markets[market];
  checkQuotes(input, before);
  if (batch.day === before.macro.marketAsOf && !input.refreshFinancial) return {unchanged: true};
  const names = market === 'TW' ? ['0052'] : ['QQQ', 'SOXX', 'IGV'];
  const benchmarks = {};
  for (const name of names) {
    benchmarks[name] = await yahoo(market === 'TW' ? '0052.TW' : name, market, batch.day);
    if (benchmarks[name].at(-1)?.date !== batch.day) throw Error('基準行情尚未到齊：' + name);
  }
  progress({stage: 'macro'});
  // Same original macro rules, including the already approved CPI overlay.
  const macro = await refreshMacro(batch.day, undefined, market, false, undefined, true);
  if (before.macro.poolCheck) macro.poolCheck = before.macro.poolCheck;
  const now = new Date().toISOString();
  macro.marketComplete = true;
  macro.lastMarketPublishedAt = now;
  const context = {market, cutoff: batch.day, benchmarks, macro, createdAt: now, liveFinancial: true};
  const stocks = [], bars = {}, financialVersions = {};
  let reused = 0, changed = 0;
  const groupSize = market === 'US' ? 1 : 3; // SEC facts stay within the existing free service's memory.
  for (let offset = 0; offset < before.stocks.length; offset += groupSize) {
    const results = await Promise.all(before.stocks.slice(offset, offset + groupSize).map(async old => {
      const inactive = confirmedInactive(old, batch.day);
      if (inactive) return inactive;
      // Daily version comparison, not a repeat financial audit. Preserve all
      // existing financial evidence and observation timestamps when unchanged.
      const version = input.financialVersions?.[old.symbol];
      const saved = runtime.financialVersions?.[old.symbol];
      const reuse = market === 'TW' && version && version.period === old.financial.latest_period &&
        version.token === saved?.token;
      if (market === 'TW' && !version) throw Error('最新財報來源未涵蓋：' + old.symbol);
      const result = await refreshInstrument(old, context, Boolean(reuse), runtime.bars?.[old.symbol] || []);
      const q = batch.quotes[old.symbol];
      if (!q || Math.abs(q.close - result.close) / result.close > 0.001)
        throw Error('Google 與歷史行情收盤不一致：' + old.symbol);
      const last = result._bars.at(-1), adjustment = last.adjClose / last.close;
      last.close = q.close;
      last.adjClose = q.close * adjustment;
      result.close = q.close;
      result.history = result._bars.slice(-90).map(b => ({date: b.date, close: b.adjClose}));
      result.features = {...result.features,
        ...priceFeatures(result._bars, benchmarks[old.benchmark] || benchmarks[market === 'TW' ? '0052' : 'QQQ'])};
      result.priceSource = 'Google Finance／交易所行情；既有歷史行情來源';
      result.priceValidation = {...result.priceValidation, mode: 'google_corroborated'};
      if (version) financialVersions[old.symbol] = version;
      if (reuse) reused++; else changed++;
      return result;
    }));
    for (const row of results) {
      const {_bars, ...stock} = row;
      stocks.push(stock);
      if (_bars) bars[stock.symbol] = _bars;
    }
    progress({stage: 'instruments', completed: stocks.length, expected: before.stocks.length});
  }
  const scores = rankInstruments(stocks, macro, batch.day).filter(s => s.rankScore !== null)
    .sort((a, b) => b.rankScore - a.rankScore || a.key.localeCompare(b.key));
  const ranks = new Map(scores.map((s, i) => [s.key, {rank: i + 1, score: s.rankScore,
    detail: s.rankDetail, date: batch.day, archived: false, divergence: evaluate(s, macro).divergence}]));
  const instruments = stocks.map(s => ({...s, evaluation: evaluate(s, macro), researchRank: ranks.get(s.key) || null}));
  requirePublishable(verifyMarketInputs(instruments, before.stocks));
  const job = {market, status: 'done', total: before.stocks.length, updated: stocks.length,
    failed: 0, created_at: now, updated_at: now, retained: 0, fallback: 0};
  const dashboard = {authenticated: false, storageReady: true, serverTime: now,
    jobs: [job, ...prior.snapshot.jobs].slice(0, 16),
    instruments: [...instruments, ...Object.entries(prior.snapshot.markets)
      .filter(([key]) => key !== market).flatMap(([, block]) => block.stocks)],
    macros: {...Object.fromEntries(Object.entries(prior.snapshot.markets).map(([key, b]) => [key, b.macro])), [market]: macro},
    strategySignalTimes: {...Object.fromEntries(Object.entries(prior.snapshot.markets).map(([key, b]) => [key, b.signalAt])), [market]: now}};
  const baseline = JSON.parse(await readFile(new URL('../data/tracking-baseline.json', import.meta.url)));
  const snapshot = project(dashboard, baseline, prior.snapshot.qa);
  const packed = envelope(snapshot);
  return {schema: 1, market, day: batch.day, snapshot, rows: packed.rows,
    batch: packed.id, checksum: packed.checksum, financialReused: reused, financialRefreshed: changed,
    runtime: {bars, financialVersions, completedDay: batch.day}, generatedAt: now};
}

if (process.argv[1] && new URL(import.meta.url).pathname === process.argv[1]) {
  const [inputPath, outputPath] = process.argv.slice(2);
  try {
    const result = await calculate(JSON.parse(await readFile(inputPath)), value =>
      console.log(JSON.stringify({pipeline: true, ...value})));
    await writeFile(outputPath, JSON.stringify(result));
  } catch (error) {
    console.error(JSON.stringify({pipeline: true, stage: 'failed', error: String(error.message).slice(0, 500)}));
    process.exitCode = 1;
  }
}
