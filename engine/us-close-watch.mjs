import {readFile, writeFile} from 'node:fs/promises';

const dayms = 86400000;
const ms = (d) => Date.parse(d.slice(0, 10) + 'T12:00:00Z');
const iso = (n) => new Date(n).toISOString().slice(0, 10);

// Same weekend rule as engine/providers.ts latestClosedDate(..., 'US').
// It does not know NYSE holidays; a holiday can look stale until the next session.
export function latestUsClosedDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en-CA', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', hourCycle: 'h23',
  }).formatToParts(now);
  const val = (type) => parts.find((part) => part.type === type).value;
  const today = `${val('year')}-${val('month')}-${val('day')}`;
  let eligible = Number(val('hour')) >= 17 ? today : iso(ms(today) - dayms);
  while ([0, 6].includes(new Date(ms(eligible)).getUTCDay())) eligible = iso(ms(eligible) - dayms);
  return eligible;
}

function inactive(stock, asOf) {
  const event = stock?.marketEvent;
  return Boolean(event?.effectiveDate && asOf >= event.effectiveDate);
}

export function usQuoteGaps(snapshot) {
  const asOf = snapshot?.markets?.US?.macro?.marketAsOf;
  if (!asOf) return [];
  return (snapshot.markets?.US?.stocks || []).flatMap((stock) => {
    if (stock.priceDate === asOf || inactive(stock, asOf)) return [];
    return [{symbol: stock.symbol, priceDate: stock.priceDate || null}];
  });
}

export function assessUsClose(release, now = new Date()) {
  const attempt = release?.attempts?.US || (release?.attempt?.market === 'US' ? release.attempt : null);
  const expected = latestUsClosedDate(now);
  const usClose = attempt?.us_close || release?.snapshot?.markets?.US?.macro?.marketAsOf || null;
  const status = attempt?.status || 'missing';
  const attemptAt = attempt?.at || null;
  const gaps = usQuoteGaps(release?.snapshot);
  const nominalStart = `${expected}T22:17:00.000Z`;
  const base = {expected, usClose, status, attemptAt, quoteGaps: gaps, nominalStart};
  if (!attempt) {
    return {...base, ok: false, code: 'NOT_TRIGGERED',
      detail: `沒有美股更新紀錄。日曆預期收盤 ${expected}。`};
  }
  if (status === 'failed' || !['published', 'same_close'].includes(status)) {
    return {...base, ok: false, code: 'UPDATE_FAILED',
      detail: `最近美股更新狀態是 ${status}，不是已發布。收盤日 ${usClose || '無'}，預期 ${expected}。${attempt.detail || ''}`};
  }
  if (usClose < expected) {
    const triggered = attemptAt && attemptAt >= nominalStart;
    return {...base, ok: false, code: triggered ? 'STALE' : 'NOT_TRIGGERED',
      detail: triggered
        ? `排程或備援已跑過，但美股收盤仍是 ${usClose}，低於日曆預期 ${expected}。若當天是美股休市，這是日曆限制而不是覆寫。`
        : `名義更新時間 ${nominalStart} 之後還沒有新的美股嘗試。最後紀錄 ${attemptAt}，收盤仍是 ${usClose}，預期 ${expected}。`};
  }
  if (gaps.length) {
    return {...base, ok: false, code: 'QUOTE_GAP',
      detail: `已發布收盤 ${usClose}，但 ${gaps.length} 檔仍缺該日行情且沒有停止交易事件：${gaps.map((gap) => gap.symbol + '@' + gap.priceDate).join(', ')}。`};
  }
  return {...base, ok: true, code: 'CURRENT',
    detail: `美股收盤 ${usClose} 已涵蓋預期日 ${expected}。狀態 ${status}。停止交易標的不計入缺漏。`};
}

async function main() {
  const [input, output] = process.argv.slice(2);
  if (!input || !output) throw new Error('使用方式：node engine/us-close-watch.mjs release.json result.json');
  const release = JSON.parse(await readFile(input, 'utf8'));
  const result = assessUsClose(release, new Date());
  await writeFile(output, JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify(result));
  if (!result.ok) process.exitCode = 1;
}

if (process.argv[1] && process.argv[1].endsWith('us-close-watch.mjs')) await main();
