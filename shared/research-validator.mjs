import contract from './research-contract.json' with { type: 'json' };

export const MARKETS = Object.keys(contract.counts);
const dayMs = 86400000;
const date = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(v) &&
  Number.isFinite(Date.parse(v)) && new Date(v).toISOString().slice(0, 10) === v;
const stamp = v => typeof v === 'string' && /^\d{4}-\d{2}-\d{2}T/.test(v) && Number.isFinite(Date.parse(v));
const day = (clock, timeZone) => new Intl.DateTimeFormat('en-CA', {timeZone, year:'numeric', month:'2-digit', day:'2-digit'}).format(clock);
const age = (a, b) => (Date.parse(a) - Date.parse(b)) / dayMs;
const object = v => v !== null && typeof v === 'object' && !Array.isArray(v);
const ensure = (ok, reason) => { if (!ok) throw new Error(reason); };
const exact = (obj, keys) => object(obj) && Object.keys(obj).length === keys.length && keys.every(k => Object.hasOwn(obj,k));
const unsafe = new Set(contract.privateFields.map(k => k.toLowerCase()));
const finite = Number.isFinite;

function verifyPrivacy(value, path = []) {
  if (!value || typeof value !== 'object') return;
  for (const [key, child] of Object.entries(value)) {
    const simulated = path.length === 4 && path[0] === 'markets' && path[2] === 'marks';
    ensure(!unsafe.has(key.toLowerCase()) && (key !== 'portfolios' || simulated), '研究快照包含私人欄位');
    verifyPrivacy(child, [...path, key]);
  }
}

export function validateResearch(snapshot, clock = new Date(), expected = {}) {
  ensure(exact(snapshot,['schema','sourceVersion','historyThrough','generatedAt','qa','markets','jobs']) &&
    snapshot.schema === contract.schema && snapshot.sourceVersion === contract.sourceVersion &&
    snapshot.historyThrough === contract.historyThrough && stamp(snapshot.generatedAt) &&
    Date.parse(snapshot.generatedAt) <= +clock + 300000 && exact(snapshot.markets, MARKETS) &&
    Array.isArray(snapshot.jobs), '研究資料版本、欄位或時間無效');
  verifyPrivacy(snapshot);
  for (const market of MARKETS) {
    const block = snapshot.markets[market], {stocks, macro, signalAt} = block;
    const cutoff = macro.marketAsOf;
    const marketDay = day(clock, market === 'US' ? 'America/New_York' : 'Asia/Taipei');
    ensure(exact(block,['stocks','macro','marks','signalAt']) && object(block.marks) &&
      macro.marketComplete === true && macro.ruleVersion === contract.strategy && date(cutoff) &&
      cutoff <= marketDay && ![0,6].includes(new Date(cutoff).getUTCDay()) && (!expected[market] || cutoff >= expected[market]) &&
      stamp(macro.lastMarketPublishedAt) && Date.parse(macro.lastMarketPublishedAt) <= Date.parse(snapshot.generatedAt) &&
      (signalAt === null || (stamp(signalAt) && Date.parse(signalAt) <= Date.parse(snapshot.generatedAt))) &&
      Array.isArray(stocks) && stocks.length === contract.counts[market] &&
      new Set(stocks.map(s => s.symbol)).size === stocks.length, market+' 市場快照不完整或交易日不符');
    const categories = ['rates','conditions','inflation'];
    ensure(exact(macro.categoryStates, categories), market+' 資金類別不符');
    const lights = categories.map(k => macro.categoryStates[k]);
    const min = lights.filter(x => x === 1).length, max = min + lights.filter(x => x === null).length;
    ensure(lights.every(x => [0,1,null].includes(x)) && JSON.stringify(macro.scoreRange) === JSON.stringify([min,max]) &&
      macro.decision === (min >= 2 ? 'red' : max < 2 ? 'non_red' : 'unknown') &&
      (min === max ? macro.status === 'vintage_verified' && macro.macro_stress === min :
        macro.status === 'macro_incomplete' && macro.macro_stress === null), market+' 資金狀態不一致');
    const ranked = [];
    for (const stock of stocks) {
      const {symbol, priceDate, marketEvent, financial:f, evaluation:e, researchRank:r, features} = stock;
      const msg = market+':'+symbol;
      const inactive = marketEvent && date(marketEvent.effectiveDate) && priceDate < marketEvent.effectiveDate && marketEvent.effectiveDate <= cutoff;
      ensure(typeof symbol === 'string' && symbol.length > 0 && stock.market === market && stock.key === msg &&
        date(priceDate) && finite(stock.close) && stock.close > 0 && stock.integrity === true && !stock.sourceError &&
        (inactive ? !r && e?.fresh === false : !marketEvent && priceDate === cutoff &&
          stock.priceValidation?.cutoff === cutoff && e?.fresh === true), msg+' 行情不完整或交易日不一致');
      ensure(date(f?.latest_period) && f.latest_period <= priceDate && typeof f.fundamental_release_key === 'string' &&
        f.fundamental_release_key.startsWith(f.latest_period+'|') &&
        (market === 'US' ? date(f.max_input_filed) && age(priceDate,f.max_input_filed) >= 2 :
          !f.max_input_filed || (date(f.max_input_filed) && f.max_input_filed <= priceDate)) &&
        (f.financial_status !== 'ok' || ['revenue_growth','quarterly_growth','operating_margin','cfo_margin'].every(k => finite(f[k]))),
        msg+' 財報期別、欄位或版本無效');
      const ev = f.filingEvidence;
      const close = Date.parse(priceDate+'T13:30:00+08:00');
      if (market === 'TW') ensure(e?.filingVerified === Boolean(ev && ev.period === f.latest_period &&
        ev.releaseKey === f.fundamental_release_key && stamp(ev.publishedAt) && Date.parse(ev.publishedAt) <= close &&
        stamp(ev.verifiedThrough) && Date.parse(ev.verifiedThrough) >= close && /^https:\/\//.test(ev.source)), msg+' 申報驗證旗標不一致');
      ensure(contract.flags.every(k => typeof e?.[k] === 'boolean') && object(features) &&
        e.funding === (macro.decision === 'non_red') &&
        e.divergence === (finite(features.drawdown_126) && features.drawdown_126 >= -.5 &&
          features.drawdown_126 <= -.1 && finite(features.relative_63) && features.relative_63 < 0) &&
        (!e.fundamental || f.financial_status === 'ok') &&
        Boolean(r) === (e.fundamental && e.tradable && e.fresh) &&
        e.primary === (e.fundamental && e.filingVerified && e.funding && e.tradable && e.fresh) &&
        e.buy_hold === (e.fundamental && e.filingVerified && e.tradable && e.fresh && e.divergence &&
          macro.status === 'vintage_verified' && finite(macro.legacyStress) && macro.legacyStress <= 1), msg+' 策略旗標不一致');
      if (!r) continue;
      const parts = r.detail?.parts, weights = contract.weights[market];
      ensure(r.date === cutoff && r.archived === false && r.divergence === e.divergence &&
        r.detail?.financialPeriod === f.latest_period && r.detail?.filingDate === (ev?.publishedAt ?? f.max_input_filed ?? null) &&
        finite(r.score) && Array.isArray(parts) && parts.length === Object.keys(weights).length &&
        new Set(parts.map(p => p.key)).size === parts.length &&
        parts.every(p => Object.hasOwn(weights,p.key) && finite(p.value) && finite(p.weight) && Math.abs(p.weight-weights[p.key]) < 1e-12 &&
          finite(p.percentile) && p.percentile >= 0 && p.percentile <= 1) &&
        Math.abs(r.score-parts.reduce((sum,p) => sum+p.weight*p.percentile*100,0)) < 1e-8, msg+' 排名、因子或財報版本不一致');
      ranked.push(r);
    }
    ranked.sort((a,b) => a.rank-b.rank);
    ensure(ranked.every((r,i) => r.rank === i+1 && (i===0 || r.score <= ranked[i-1].score + 1e-8)), market+' 排名順序不一致');
  }
  return snapshot;
}

