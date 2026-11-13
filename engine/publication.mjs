/** Keep the last verified ranking if acquisition cannot prove the next one. */
export function verifyMarketInputs(stocks, before) {
  const previous = new Map(before.map(stock => [stock.key, stock]));
  const counts = {quotes:0, financialUnchanged:0, financialUpdated:0, financialPending:0, filingPending:0};
  const errors = [];
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
      if (stock.market === 'TW' && !stock.evaluation.filingVerified)
        errors.push({symbol:stock.symbol,code:'MISSING_FILING_EVIDENCE',message:'新財報版本缺少收盤前公告與版本證據'});
    }
  }
  return {counts, errors};
}

export function requirePublishable(result) {
  if (result.errors.length) throw new Error('財報版本待核對，保留前批：'+result.errors.map(({symbol,message})=>symbol+' '+message).join('｜'));
}
