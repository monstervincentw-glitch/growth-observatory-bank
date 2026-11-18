import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {US_RESEARCH_CRONS, TW_RESEARCH_CRONS, marketForResearchSchedule} from '../engine/research-schedule.mjs';
import {assessUsClose, latestUsClosedDate} from '../engine/us-close-watch.mjs';

const workflow = await readFile(new URL('../.github/workflows/research-update.yml', import.meta.url), 'utf8');
const marketLine = workflow.split('\n').find((line) => line.includes('MARKET:'));

test('US backup clocks stay on the US side of the scheduler', () => {
  assert.deepEqual(US_RESEARCH_CRONS, ['17 22 * * 1-5', '0 23 * * 1-5', '20 23 * * 1-5']);
  for (const cron of US_RESEARCH_CRONS) {
    assert.equal(marketForResearchSchedule(cron), 'US');
    assert.match(workflow, new RegExp(`cron: '${cron.replaceAll('*', '\\*')}'`));
    assert.match(marketLine, new RegExp(cron.replaceAll('*', '\\*')));
  }
  for (const cron of TW_RESEARCH_CRONS) {
    assert.equal(marketForResearchSchedule(cron), 'TW');
    assert.match(workflow, new RegExp(`cron: '${cron.replaceAll('*', '\\*')}'`));
    assert.equal(marketLine.includes(`'${cron}'`), false);
  }
});

const now = new Date('2026-11-17T23:45:00Z');
const stock = (symbol, priceDate, marketEvent) => ({symbol, priceDate, marketEvent});
const release = (attempt, stocks) => ({
  attempt, attempts: {US: attempt},
  snapshot: {markets: {US: {macro: {marketAsOf: attempt.us_close}, stocks}}},
});

test('Taipei Wednesday morning expects the Tuesday US close', () => {
  assert.equal(latestUsClosedDate(now), '2026-11-17');
});

test('backup same_close of an already published US session is healthy', () => {
  const result = assessUsClose(release({
    at: '2026-11-17T23:20:00.000Z', market: 'US', status: 'same_close', us_close: '2026-11-17',
  }, [stock('AAA', '2026-11-17'), stock('ROKU', '2026-11-06', {effectiveDate: '2026-11-09'})]), now);
  assert.equal(result.ok, true);
  assert.equal(result.code, 'CURRENT');
});

test('no run after the nominal US slot is a missed trigger, not a successful close', () => {
  const result = assessUsClose(release({
    at: '2026-11-16T23:02:00.000Z', market: 'US', status: 'published', us_close: '2026-11-16',
  }, [stock('AAA', '2026-11-16')]), now);
  assert.equal(result.ok, false);
  assert.equal(result.code, 'NOT_TRIGGERED');
});

test('failed US attempt stays an alert even when an older close exists', () => {
  const result = assessUsClose(release({
    at: '2026-11-17T23:05:00.000Z', market: 'US', status: 'failed', us_close: '2026-11-16', detail: '來源中斷',
  }, [stock('AAA', '2026-11-16')]), now);
  assert.equal(result.code, 'UPDATE_FAILED');
});

test('a run that did not advance the close is stale rather than unpublished', () => {
  const result = assessUsClose(release({
    at: '2026-11-17T23:20:00.000Z', market: 'US', status: 'same_close', us_close: '2026-11-16',
  }, [stock('AAA', '2026-11-16')]), now);
  assert.equal(result.code, 'STALE');
});

test('a live symbol left behind the published close is a quote gap', () => {
  const result = assessUsClose(release({
    at: '2026-11-17T23:10:00.000Z', market: 'US', status: 'published', us_close: '2026-11-17',
  }, [stock('AAA', '2026-11-17'), stock('BBB', '2026-11-16')]), now);
  assert.equal(result.code, 'QUOTE_GAP');
  assert.deepEqual(result.quoteGaps, [{symbol: 'BBB', priceDate: '2026-11-16'}]);
});
