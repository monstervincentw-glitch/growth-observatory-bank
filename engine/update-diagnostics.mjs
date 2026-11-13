/** Diagnostics for the existing close updater. No retries or provider changes. */
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
  try {
    const response = await fetch(url, options);
    if (!response.ok) diagnostic?.(response.status, provider);
    return response;
  } catch (error) {
    const timeout = options.signal?.aborted || ['TimeoutError','AbortError'].includes(error?.name)
      || ['ETIMEDOUT','UND_ERR_CONNECT_TIMEOUT','UND_ERR_HEADERS_TIMEOUT'].includes(error?.cause?.code);
    diagnostic?.(timeout ? 'TIMEOUT' : 'FETCH_FAILED', provider);
    throw error;
  }
}
