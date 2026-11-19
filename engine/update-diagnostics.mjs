/** Provider circuit breaker: never repeatedly hit a host after 429/403. */
const blockedHosts=new Map();
export function createDiagnostics(clock, runId) {
  const errors = [];
  function record(context, code, provider = null) {
    if (!context) return;
    errors.push({...context, code, provider, at:clock(), runId});
  }
  return {errors, record};
}

export async function monitoredFetch(url, options, diagnostic) {
  const provider = new URL(url).hostname;
  const blocked=blockedHosts.get(provider);
  if(blocked && blocked > Date.now()){
    diagnostic?.('RATE_LIMIT_UNTIL:'+new Date(blocked).toISOString(),provider);
    throw new Error('來源冷卻中：'+provider);
  }
  try {
    const response = await fetch(url, options);
    if([429,403].includes(response.status)){
      const retry=response.headers.get('retry-after');
      const parsed=retry && (/^\d+$/.test(retry)?Date.now()+Number(retry)*1000:Date.parse(retry));
      const until=Math.max(parsed||0,Date.now()+(response.status===403?21600000:3600000));
      blockedHosts.set(provider,until);
      diagnostic?.('RATE_LIMIT_UNTIL:'+new Date(until).toISOString(),provider);
    }
    if (!response.ok) diagnostic?.(response.status, provider);
    return response;
  } catch (error) {
    const timeout = options.signal?.aborted || ['TimeoutError','AbortError'].includes(error?.name)
      || ['ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_HEADERS_TIMEOUT'].includes(error?.cause?.code);
    diagnostic?.(timeout ? 'TIMEOUT' : 'FETCH_FAILED', provider);
    throw error;
  }
}
