/** Keep the last verified ranking if acquisition cannot prove the next one. */
export function verifyMarketInputs(stocks, before) {
  const previous = new Map(before.map(stock => [stock.key, stock]));
  const counts = {quotes:0, financialUnchanged:0, financialUpdated:0, financialPending:0, filingPending:0};
  const errors = [];
  const warnings = [];
  for (const stock of stocks) {
    const old = previous.get(stock.key);
    const f = stock.financial;
    counts.quotes += Number(!stock.marketEvent);
    if (stock.market === 'TW' && !stock.evaluation.filingVerified) counts.filingPending++;
    if (stock.marketEvent) continue;
    if (f.financial_status !== 'ok') {
      counts.financialPending++;
      if (old?.financial.financial_status === 'ok') errors.push({symbol:stock.symbol,code:'INCOMPLETE_FINANCIAL',message:'新取得財報欄位不足'});
    }
    if (old?.financial.latest_period && f.latest_period < old.financial.latest_period)
      errors.push({symbol:stock.symbol,code:'FINANCIAL_ROLLBACK',message:'財報期別倒退'});
    const unchanged = f.fundamental_release_key && old?.financial.fundamental_release_key === f.fundamental_release_key;
    if (unchanged) counts.financialUnchanged++;
    else {
      counts.financialUpdated++;
      if (stock.market === 'TW' && !stock.evaluation.filingVerified) {
        // Live research can use an observed version from first acquisition.
        // This is NOT proof that the version existed at the prior market close.
        const seen = Date.parse(stock.financialObservedAt || '');
        const captured = Date.parse(stock.observedAt || '');
        const period = Date.parse((f.latest_period || '') + 'T00:00:00Z');
        if (!Number.isFinite(seen) || !Number.isFinite(captured) ||
            !Number.isFinite(period) || seen < period || seen > captured)
          errors.push({symbol:stock.symbol,code:'INVALID_LIVE_OBSERVATION_TIME',
            message:'新財報缺少有效的首次取得時間，無法用於即時研究'});
        else
          warnings.push({symbol:stock.symbol,code:'LIVE_ONLY_NOT_PIT_VERIFIED',
            message:'已取得財報供盤後研究，未證實當日收盤前已可得；不得回填歷史成交'});
      }
    }
  }
  return {counts, errors, warnings};
}

export function requirePublishable(result) {
  if (result.errors.length) throw new Error('財報版本待核對，保留前批：'+result.errors.map(({symbol,message})=>symbol+' '+message).join('｜'));
}
